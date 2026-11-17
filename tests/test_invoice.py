"""Invoice rendering, atomic numbering and the date-only Streamlit workflow."""

import io
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import patch

from google.api_core.exceptions import AlreadyExists, ServiceUnavailable
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

from modules import invoice


class Registry:
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def collection(self, name):
        assert name == invoice.NUMBER_COLLECTION
        return self

    def document(self, code):
        registry = self

        class Document:
            def create(self, record, **kwargs):
                with registry.lock:
                    if code in registry.rows:
                        raise AlreadyExists("Code already reserved")
                    registry.rows[code] = record.copy()

        return Document()


class InvoiceTests(unittest.TestCase):
    def test_pdf_replaces_all_requested_fields_and_keeps_customer(self):
        for append_date in (False, True):
            with self.subTest(append_date=append_date):
                result = invoice.render_invoice_pdf("A1B2C", date(2026, 11, 17), append_date)
                pdf = PdfReader(io.BytesIO(result))
                source = PdfReader(invoice.TEMPLATE_PATH)
                self.assertEqual(len(pdf.pages), 1)
                self.assertEqual(pdf.pages[0].mediabox, source.pages[0].mediabox)
                text = pdf.pages[0].extract_text()
                self.assertIn(invoice.CUSTOMER, text)
                self.assertIn(invoice.DESCRIPTION, text)
                self.assertIn("17 November 2026", text)
                self.assertEqual(text.count("$85.00 SGD"), 3)
                self.assertIn("A1B2C-20261117" if append_date else "A1B2C\n", text)
                self.assertNotIn("$400.00", text)
                self.assertNotIn("50% Deposit", text)
                # Original embedded artwork and payment details are kept byte-for-byte.
                self.assertEqual(pdf.pages[0]["/Resources"]["/XObject"]["/I0"].get_data(),
                                 source.pages[0]["/Resources"]["/XObject"]["/I0"].get_data())

    def test_invalid_number_date_and_suffix_are_rejected(self):
        for code, day, append in [("ABCDE1", date.today(), False), ("ZZZZZ", date.today(), False),
                                  ("abcde", date.today(), False), ("A1B2C", "2026-11-17", False),
                                  ("A1B2C", None, False), ("A1B2C", date.today(), "custom")]:
            with self.subTest(code=code, day=day, append=append), self.assertRaises(ValueError):
                invoice.invoice_number(code, day, append)

    def test_collision_retries_across_users_and_date_suffixes(self):
        registry = Registry()
        with patch.object(invoice.secrets, "token_hex", side_effect=["abcde0", "abcde9", "000011"]), \
             patch.object(invoice, "render_invoice_pdf", return_value=b"pdf"):
            first = invoice.create_invoice(registry, "first@example.com", date(2026, 11, 17))
            second = invoice.create_invoice(registry, "second@example.com", date(2026, 12, 1), True)
        self.assertEqual(first["number"], "ABCDE")
        self.assertEqual(second["number"], "00001-20261201")
        self.assertEqual(len(registry.rows), 2)
        self.assertEqual(registry.rows["ABCDE"]["number"], "ABCDE")
        self.assertNotEqual(first["owner"], second["owner"])
        self.assertEqual(second["amount_cents"], 8500)
        self.assertNotIn("pdf", registry.rows["00001"])

    def test_concurrent_generations_reserve_distinct_codes(self):
        registry = Registry()
        barrier = threading.Barrier(2)
        local = threading.local()
        tokens = iter(["000011", "000022"])
        lock = threading.Lock()

        def candidate(_):
            if not getattr(local, "attempted", False):
                local.attempted = True
                barrier.wait(timeout=5)
                return "abcde0"
            with lock:
                return next(tokens)

        with patch.object(invoice.secrets, "token_hex", side_effect=candidate), \
             patch.object(invoice, "render_invoice_pdf", return_value=b"pdf"), \
             ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda email: invoice.create_invoice(registry, email, date.today()),
                                    ["a@example.com", "b@example.com"]))
        self.assertEqual(len({result["code"] for result in results}), 2)
        self.assertEqual(len(registry.rows), 2)

    def test_repeated_collisions_fail_without_overwriting_existing_invoice(self):
        registry = Registry()
        registry.rows["ABCDE"] = {"existing": True}
        with patch.object(invoice.secrets, "token_hex", return_value="abcde0"), \
             patch.object(invoice, "render_invoice_pdf", return_value=b"pdf"), \
             self.assertRaisesRegex(RuntimeError, "unique invoice number"):
            invoice.create_invoice(registry, "a@example.com", date.today())
        self.assertEqual(registry.rows, {"ABCDE": {"existing": True}})

    def test_storage_and_render_failures_do_not_return_an_invoice(self):
        registry = Registry()
        with patch.object(invoice, "render_invoice_pdf", side_effect=ValueError("Bad template")), \
             self.assertRaises(ValueError):
            invoice.create_invoice(registry, "a@example.com", date.today())
        self.assertFalse(registry.rows)
        with patch.object(registry, "collection", side_effect=ServiceUnavailable("Offline")), \
             patch.object(invoice, "render_invoice_pdf", return_value=b"pdf") as render, \
             self.assertRaises(ServiceUnavailable):
            invoice.create_invoice(registry, "a@example.com", date.today())
        render.assert_called_once()


class InvoiceUITests(unittest.TestCase):
    script = "from modules.invoice_ui import render_invoice_workspace\nrender_invoice_workspace('editor@example.com')"

    def setUp(self):
        self.registry = Registry()
        for patcher in (
            patch("modules.database.init_db", return_value=self.registry),
            patch.object(invoice, "current_invoice_date", return_value=date(2026, 11, 17)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def app(self):
        app = AppTest.from_string(self.script).run()
        self.assertFalse(list(app.exception))
        return app

    def test_only_date_and_suffix_are_editable_and_loading_does_not_reserve_code(self):
        app = self.app()
        self.assertEqual(app.date_input(key="invoice_date").value, date(2026, 11, 17))
        self.assertEqual(len(app.date_input), 1)
        self.assertEqual(len(app.text_input), 0)
        self.assertEqual(len(app.number_input), 0)
        self.assertFalse(app.checkbox(key="invoice_append_date").value)
        self.assertEqual(app.metric[0].value, "$85.00")
        self.assertFalse(self.registry.rows)

    def test_generate_download_rerun_change_date_and_new_invoice(self):
        app = self.app()
        app.date_input(key="invoice_date").set_value(date(2026, 12, 3)).run()
        app.checkbox(key="invoice_append_date").check().run()
        app.button(key="invoice_generate").click().run()
        self.assertFalse(list(app.exception))
        result = app.session_state["invoice_result"]
        self.assertTrue(result["number"].endswith("-20261203"))
        self.assertEqual(len(app.get("download_button")), 1)
        self.assertTrue(result["pdf"].startswith(b"%PDF"))
        self.assertNotIn("invoice_generate", [button.key for button in app.button])
        app.run()
        self.assertEqual(len(self.registry.rows), 1)
        self.assertEqual(app.session_state["invoice_result"]["number"], result["number"])
        # An outdated download is removed as soon as either input changes.
        app.date_input(key="invoice_date").set_value(date(2026, 12, 4)).run()
        self.assertEqual(len(app.get("download_button")), 0)
        app.button(key="invoice_generate").click().run()
        self.assertEqual(len(self.registry.rows), 2)
        app.button(key="invoice_new").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(app.date_input(key="invoice_date").value, date(2026, 11, 17))
        self.assertFalse(app.checkbox(key="invoice_append_date").value)
        self.assertEqual(len(app.get("download_button")), 0)

    def test_suffix_change_discards_old_download(self):
        app = self.app()
        app.button(key="invoice_generate").click().run()
        app.checkbox(key="invoice_append_date").check().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(len(app.get("download_button")), 0)
        self.assertEqual(len(self.registry.rows), 1)

    def test_database_error_shows_no_download_and_can_be_retried(self):
        app = self.app()
        with patch("modules.database.init_db", side_effect=ServiceUnavailable("Private diagnostic")):
            app.button(key="invoice_generate").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(len(app.get("download_button")), 0)
        self.assertEqual(len(app.error), 1)
        self.assertNotIn("Private diagnostic", app.error[0].value)
        app.button(key="invoice_generate").click().run()
        self.assertEqual(len(app.get("download_button")), 1)


if __name__ == "__main__":
    unittest.main()
