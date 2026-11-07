"""PDF export of the client report: rendering, isolation, failures and the dashboard flow."""

import asyncio
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules import report_pdf
from modules.client_report import report_document, report_record
from modules.report_pdf import MISSING_BROWSER, PDFExportError, render_pdf
from tests.analysis_steps import ui


def negative(ident, risk=9):
    return {"_id": ident, "url": f"https://www.facebook.com/groups/1/posts/{ident}/", "source_type": "container_post",
            "status": "processed", "processed_at": "2026-11-02T09:00:00+00:00",
            "raw_text": "ឈប់ទិញផលិតផលថៃ\n12 comments", "scraped_at": "2026-11-01T00:00:00+00:00",
            "analysis": {"sentiment": "Anger", "risk_score": risk, "topics": ["boycott"],
                         "translation": "Stop buying Thai products",
                         "monitor": {"thailand_relevance": "explicit", "signal_type": "boycott_call",
                                     "target_names": [], "confidence": "high", "summary": "Boycott call",
                                     "evidence": [{"source": "raw_text", "quote": "ឈប់", "purpose": "signal"}]}}}


class Route:
    def __init__(self, url):
        self.request = SimpleNamespace(url=url)
        self.outcome = None

    async def continue_(self):
        self.outcome = "continue"

    async def abort(self):
        self.outcome = "abort"


class ReportPdfTests(unittest.TestCase):
    def test_renders_one_page_per_template_and_report(self):
        records = [report_record(negative("aa1111")), report_record(negative("bb2222", 6))]
        try:
            pdf = render_pdf(report_document(records, "2026-11-02"), "CrisisRadar report · 2026-11-02")
        except PDFExportError as exc:
            if str(exc) == MISSING_BROWSER:
                self.skipTest("Chromium is not installed for this Playwright version")
            raise
        self.assertTrue(pdf.startswith(b"%PDF"))
        # Comment Sentiment Analysis, then one Negative Post Report each.
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf)), 3)

    def test_page_loads_only_google_fonts(self):
        allowed = ["https://fonts.googleapis.com/css2?family=Inter", "https://fonts.gstatic.com/s/inter/a.woff2",
                   "data:font/woff2;base64,AAAA"]
        blocked = ["https://example.com/pixel.png", "http://fonts.googleapis.com/css2", "file:///etc/passwd",
                   "https://fonts.googleapis.com.evil.test/x", "http://169.254.169.254/latest/meta-data"]
        for url in allowed + blocked:
            route = Route(url)
            asyncio.run(report_pdf._only_fonts(route))
            self.assertEqual(route.outcome, "continue" if url in allowed else "abort", url)

    def test_missing_browser_explains_the_fix(self):
        async def launch(**kwargs):
            raise RuntimeError("Executable doesn't exist at /ms-playwright/chromium")

        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        with self.assertRaises(PDFExportError) as caught:
            asyncio.run(report_pdf._launch(playwright))
        self.assertEqual(str(caught.exception), MISSING_BROWSER)

    def test_unexpected_failures_show_a_safe_message(self):
        async def broken(document, label):
            raise RuntimeError("/internal/path secret detail")

        with patch("modules.report_pdf._render", broken), self.assertLogs("modules.report_pdf", "ERROR"):
            with self.assertRaises(PDFExportError) as caught:
                render_pdf("<html></html>")
        self.assertNotIn("secret", str(caught.exception))
        self.assertIn("HTML report", str(caught.exception))


class DashboardPdfTests(unittest.TestCase):
    def setUp(self):
        access = patch("modules.auth.verify_access", return_value="editor@example.com")
        access.start()
        self.addCleanup(access.stop)

    def app(self):
        path = str(Path(__file__).resolve().parents[1] / "dashboard.py")
        app = AppTest.from_file(path)
        app.run(timeout=30)
        ui(app, "analysis_sources").select("source:facebook").run(timeout=30)
        app.session_state["analysis_dataset_catalog"] = {"rows": [{"key": "untitled:", "title": "Untitled", "count": 2, "pending": 0}], "error": False}
        app.run(timeout=30)
        ui(app, "analysis_datasets").select("untitled:").run(timeout=30)
        app.session_state["firestore_rows"] = [negative("aa1111")]
        app.run(timeout=30)
        self.assertFalse(list(app.exception))
        return app

    def labels(self, app):
        return [element.proto.label for element in app.get("download_button")]

    def test_create_pdf_then_download_until_the_report_changes(self):
        with patch.dict(os.environ, {"PLAYWRIGHT_STORAGE_STATE": "/tmp/crisisradar-no-session-for-pdf-test.json"}), \
                patch("modules.config.load_environment"), \
                patch("modules.auth.require_access", return_value="editor@example.com"), \
                patch("modules.database.init_db"), \
                patch("modules.saved_links.load_saved_links", return_value=[]), \
                patch("modules.report_pdf.render_pdf", return_value=b"%PDF-1.4 test") as render:
            app = self.app()
            self.assertNotIn("Download PDF", self.labels(app))
            app.button(key="saved_report_pdf_create").click().run(timeout=30)
            self.assertFalse(list(app.exception))
            self.assertIn("Download PDF", self.labels(app))
            render.assert_called_once()
            self.assertIn("NPR-AA1111", render.call_args.args[0])
            app.session_state["firestore_rows"] = [negative("aa1111"), negative("cc3333")]
            app.run(timeout=30)
            self.assertNotIn("Download PDF", self.labels(app))
            self.assertEqual(render.call_count, 1)

    def test_pdf_failure_is_shown_and_html_stays_available(self):
        with patch.dict(os.environ, {"PLAYWRIGHT_STORAGE_STATE": "/tmp/crisisradar-no-session-for-pdf-test.json"}), \
                patch("modules.config.load_environment"), \
                patch("modules.auth.require_access", return_value="editor@example.com"), \
                patch("modules.database.init_db"), \
                patch("modules.saved_links.load_saved_links", return_value=[]), \
                patch("modules.report_pdf.render_pdf", side_effect=PDFExportError(MISSING_BROWSER)):
            app = self.app()
            app.button(key="saved_report_pdf_create").click().run(timeout=30)
            self.assertIn(MISSING_BROWSER, [error.value for error in app.error])
            self.assertIn("Download report (HTML)", self.labels(app))
            self.assertNotIn("Download PDF", self.labels(app))


if __name__ == "__main__":
    unittest.main()
