"""PDF export of a velocity card: the printable document, the PDF and the notepad flow."""

import re
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules import velocity_tracking as store
from modules.report_pdf import MISSING_BROWSER, PDFExportError, render_pdf
from modules.saved_links import library_owner_id
from modules.velocity_export import RUNS_PER_PAGE, card_document, card_pdf_name
from modules.velocity_table import velocity_card_sheet
from tests.test_velocity_tracking import VelocityTestCase, post_url

EMAIL = "owner@example.com"


def run(number, posts, counts):
    return {"id": f"{number:064x}", "number": number, "at": f"2026-11-{number:02}T01:00:00+00:00",
            "metric": "reactions_comments",
            "results": [{"post_id": post["id"], "reactions": count, "comments": 0, "outcome": "ok",
                         "comment_analysis": {"status": "ok", "analyzed_count": 4, "collected_count": 4,
                                              "counts": {"negative": 1, "neutral": 1, "positive": 2},
                                              "keywords": [{"word": "refund", "count": 3}]}}
                        for post, count in zip(posts, counts)]}


def card_with_runs(count):
    posts = [{"id": f"p{index}", "url": post_url(index), "remark": "Escalate <b>now</b>" if index == 1 else ""}
             for index in (1, 2)]
    runs = [run(number, posts, (100 * number, 50)) for number in range(1, count + 1)]
    return {"id": "card-1", "name": "Election watch", "posts": posts, "runs": runs}


def sheets(document, label):
    return document.count(f'aria-label="{label}"')


class VelocityDocumentTests(unittest.TestCase):
    def test_every_run_is_printed_in_page_sized_column_groups(self):
        card = card_with_runs(RUNS_PER_PAGE * 2 + 1)
        document = card_document(card, "Election <watch>")
        self.assertEqual(sheets(document, "Velocity Tracker"), 3)
        self.assertEqual(sheets(document, "Comment Analysis"), 1)
        self.assertEqual(len(re.findall(r"<th scope=col data-run-id=", document)), len(card["runs"]))
        self.assertIn(f"Election &lt;watch&gt; · runs 11–11 of {len(card['runs'])}", document)
        self.assertNotIn("<watch>", document)
        self.assertNotIn("<b>now</b>", document)
        # Only the last velocity page carries the opened legend.
        self.assertEqual(document.count('class="crt-legend" open'), 1)
        self.assertEqual(document, card_document(card, "Election <watch>"))

    def test_a_card_without_checks_still_prints_its_posts(self):
        document = card_document(card_with_runs(0), "Election watch")
        self.assertEqual(sheets(document, "Velocity Tracker"), 1)
        self.assertIn(post_url(2), document)
        self.assertIn("No saved checks yet", document)

    def test_a_column_window_keeps_status_and_change_from_the_full_history(self):
        card = card_with_runs(4)
        full, window = velocity_card_sheet(card), velocity_card_sheet(card, columns=slice(0, 2))
        status = re.compile(r'crt-status--\w+">\w+</span></td><td title="[^"]*">[^<]+</td>')
        self.assertEqual(status.findall(full), status.findall(window))
        self.assertEqual(window.count("data-run-id="), 2)
        self.assertNotIn(">400</td>", window)

    def test_pdf_name_uses_the_card_name_and_latest_check(self):
        self.assertEqual(card_pdf_name(card_with_runs(3), "Election / Watch!"), "velocity-election-watch-03-nov-2026.pdf")
        self.assertEqual(card_pdf_name(card_with_runs(0), "???"), "velocity-card-no-checks.pdf")

    def test_renders_velocity_pages_then_the_analysis_page(self):
        try:
            pdf = render_pdf(card_document(card_with_runs(RUNS_PER_PAGE + 1), "Election watch"), "Election watch")
        except PDFExportError as exc:
            if str(exc) == MISSING_BROWSER:
                self.skipTest("Chromium is not installed for this Playwright version")
            raise
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf)), 3)


class VelocityPdfUITests(VelocityTestCase):
    script = f"from modules.velocity_tracking_ui import render_velocity_workspace\nrender_velocity_workspace('{EMAIL}')"
    key = f"velocity_tracking_{library_owner_id(EMAIL)}"

    def setUp(self):
        super().setUp()
        for target, value in (("modules.velocity_tracking_ui.database.init_db", self.db),
                              ("modules.auth.verify_access", EMAIL), ("modules.auth.allowed_emails", {EMAIL})):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        card = store.add_card(self.db, EMAIL, "Election watch", post_url())[0]
        post = store.card_posts(card)[0]
        store.save_run(self.db, EMAIL, card["id"], {
            "id": "a" * 64, "at": "2026-11-07T01:00:00+00:00", "metric": "reactions_comments",
            "results": [{"post_id": post["id"], "reactions": 200, "comments": 50, "outcome": "ok"}]})
        self.card_id = card["id"]

    def open(self):
        app = AppTest.from_string(self.script).run()
        app.button(key=f"{self.key}_open_{self.card_id}").click().run()
        self.assertFalse(list(app.exception))
        return app

    def downloads(self, app):
        return [element.proto.label for element in app.get("download_button")]

    def test_export_prints_once_then_offers_the_download_until_results_change(self):
        with patch("modules.velocity_tracking_ui.render_pdf", return_value=b"%PDF-1.4 test") as render:
            app = self.open()
            self.assertNotIn("Download PDF", self.downloads(app))
            app.button(key=f"velocity_pdf_create_{self.card_id}").click().run()
            self.assertFalse(list(app.exception))
            self.assertIn("Download PDF", self.downloads(app))
            render.assert_called_once()
            self.assertIn(">250</td>", render.call_args.args[0])
            self.assertEqual(render.call_args.args[1], "Election watch · Velocity card")
            store.rename_card(self.db, EMAIL, self.card_id, "Renamed watch")
            app.button(key=f"{self.key}_refresh").click().run()
            app.button(key=f"{self.key}_open_{self.card_id}").click().run()
            self.assertNotIn("Download PDF", self.downloads(app))
            self.assertEqual(app.button(key=f"velocity_pdf_create_{self.card_id}").label, "Export as PDF")
            self.assertEqual(render.call_count, 1)

    def test_export_failure_is_shown(self):
        with patch("modules.velocity_tracking_ui.render_pdf", side_effect=PDFExportError(MISSING_BROWSER)):
            app = self.open()
            app.button(key=f"velocity_pdf_create_{self.card_id}").click().run()
            self.assertFalse(list(app.exception))
            self.assertIn(MISSING_BROWSER, [error.value for error in app.error])
            self.assertNotIn("Download PDF", self.downloads(app))


if __name__ == "__main__":
    unittest.main()
