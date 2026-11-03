"""Public-page boundaries, readable extraction, partial failures and cloud integration."""

import io
import socket
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

from firebase_admin import firestore
from streamlit.testing.v1 import AppTest

from modules.websites import (
    MAX_BYTES, MAX_TEXT, WebsiteError, _get_page, _public_addresses, collect_websites,
    extract_page, normalize_website_url, parse_website_links,
)

URL = "https://example.com/article"
HTML = b'''<html><head><title>Product update</title><meta property="article:published_time" content="2026-11-03T00:00:00Z"></head>
<body><nav>Menu</nav><header>Header</header><main><h1>Product news</h1><p>Delivery delays are being resolved.</p>
<script>alert('do not collect')</script><div hidden>Secret</div><div aria-hidden="true">Hidden</div>
<span style="display: none">Invisible</span></main><footer>Footer</footer></body></html>'''


def page(payload=HTML, mime="text/html; charset=utf-8"):
    return 200, {"content-type": mime}, payload


class LinkTests(unittest.TestCase):
    def test_regular_links_are_normalized_and_deduplicated(self):
        self.assertEqual(parse_website_links("example.com/article#top\n" + URL + "\n"), [URL])
        self.assertEqual(normalize_website_url("https://EXAMPLE.com.:443/article?q=yes#top"), URL + "?q=yes")
        self.assertEqual(unquote(normalize_website_url("https://example.com/កម្ពុជា")), "https://example.com/កម្ពុជា")

    def test_social_private_and_non_web_inputs_are_rejected_before_network(self):
        values = ["https://www.facebook.com/post/1", "https://vm.tiktok.com/123", "https://reddit.com/r/test",
                  "https://x.com/i/status/1", "https://youtube.com/watch?v=1", "https://linkedin.com/company/a",
                  "http://localhost/", "http://127.0.0.1/", "http://169.254.169.254/", "http://10.0.0.1/",
                  "http://[::1]/", "file:///tmp/private", "ftp://example.com/a", "http://example.com:8000/",
                  "https://user:pass@example.com/", "https://example.com/line\rbreak", "https://app.internal/"]
        for url in values:
            with self.subTest(url=url), patch("modules.websites._get_page") as get:
                with self.assertRaises(WebsiteError):
                    collect_websites([url])
                get.assert_not_called()
        with self.assertRaisesRegex(WebsiteError, "Line 2"):
            parse_website_links(URL + "\nhttps://facebook.com/a")
        with self.assertRaisesRegex(WebsiteError, "at most 10"):
            parse_website_links("\n".join(URL + str(i) for i in range(11)))
        with self.assertRaisesRegex(WebsiteError, "at least one"):
            parse_website_links("\n")

    def test_dns_rejects_private_mixed_reserved_and_multicast_answers(self):
        for addresses in (["127.0.0.1"], ["93.184.215.14", "10.0.0.1"], ["::1"], ["224.0.0.1"],
                          ["::ffff:127.0.0.1"], ["100.64.0.1"]):
            answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443)) for a in addresses]
            with self.subTest(addresses=addresses), patch("modules.websites.socket.getaddrinfo", return_value=answer):
                with self.assertRaises(WebsiteError):
                    _public_addresses("example.com", 443)


class ExtractionTests(unittest.TestCase):
    def test_extracts_main_title_and_timestamp_and_removes_page_chrome(self):
        title, text, published, truncated = extract_page(HTML, "text/html")
        self.assertEqual(title, "Product update")
        self.assertIn("Delivery delays are being resolved.", text)
        for excluded in ("Menu", "Header", "Secret", "Hidden", "Invisible", "Footer", "alert"):
            self.assertNotIn(excluded, text)
        self.assertEqual(published, "2026-11-03T00:00:00Z")
        self.assertFalse(truncated)

    def test_body_fallback_plain_text_unicode_and_truncation(self):
        self.assertIn("Plain article", extract_page(b"<body><p>Plain article</p></body>", "text/html")[1])
        self.assertEqual(extract_page("ព័ត៌មាន & news".encode(), "text/plain")[1], "ព័ត៌មាន & news")
        _, text, _, truncated = extract_page(b"x" * (MAX_TEXT + 1), "text/plain")
        self.assertEqual(len(text), MAX_TEXT)
        self.assertTrue(truncated)
        self.assertEqual(extract_page(b"caf\xe9", "text/plain; charset=iso-8859-1")[1], "café")

    def test_challenges_empty_scripts_and_downloads_are_not_source_content(self):
        for payload, mime in [(b"pdf", "application/pdf"), (b"{}", "application/json"),
                              (b"<title>App</title><script>render()</script>", "text/html"),
                              (b"<title>Just a moment...</title><body>Verify you are human</body>", "text/html")]:
            with self.subTest(payload=payload), self.assertRaises(WebsiteError):
                extract_page(payload, mime)


class TransportTests(unittest.TestCase):
    def connection(self, payload=HTML, **headers):
        response = io.BytesIO(payload)
        response.status = 200
        response.getheaders = lambda: list({"Content-Type": "text/html", **headers}.items())
        connection = MagicMock()
        connection.getresponse.return_value = response
        return connection

    def test_connects_to_validated_ip_preserving_hostname_and_no_cookies(self):
        connection = self.connection()
        connection.request.side_effect = lambda *a, **k: connection._create_connection(("example.com", 443), 10)
        with patch("modules.websites._public_addresses", return_value=["93.184.215.14"]), \
             patch("modules.websites.http.client.HTTPSConnection", return_value=connection) as constructor, \
             patch("modules.websites.socket.create_connection") as connect:
            status, headers, payload = _get_page(URL, float("inf"))
            self.assertEqual(status, 200)
            self.assertEqual(payload, HTML)
            self.assertEqual(constructor.call_args.args[0], "example.com")  # TLS SNI and Host.
            self.assertEqual(connect.call_args.args[0], ("93.184.215.14", 443))
            sent_headers = connection.request.call_args.kwargs["headers"]
            self.assertNotIn("Cookie", sent_headers)
            self.assertNotIn("Authorization", sent_headers)
            connection.close.assert_called_once()

    def test_oversized_and_compressed_responses_are_rejected_and_closed(self):
        for connection in [self.connection(**{"Content-Length": str(MAX_BYTES + 1)}),
                           self.connection(b"x" * (MAX_BYTES + 1)), self.connection(**{"Content-Encoding": "gzip"})]:
            with patch("modules.websites._public_addresses", return_value=["93.184.215.14"]), \
                 patch("modules.websites.http.client.HTTPSConnection", return_value=connection):
                with self.assertRaises(WebsiteError):
                    _get_page(URL, float("inf"))
                connection.close.assert_called_once()


class CollectionTests(unittest.TestCase):
    def test_collects_only_supplied_pages_keeps_query_and_deduplicates_redirects(self):
        with patch("modules.websites._get_page", side_effect=[page(), (302, {"location": URL}, b""), page()]) as get:
            progress = MagicMock()
            rows = collect_websites([URL, "https://example.org/alias"], on_progress=progress)
            self.assertEqual(len(rows), 1)
            self.assertEqual(get.call_count, 3)  # No crawling links from HTML.
            self.assertEqual(progress.call_count, 2)
            self.assertEqual(rows[0]["source_type"], "website_page")
            self.assertEqual(rows[0]["platform"], "website")
            self.assertEqual(rows[0]["comments"], [])

    def test_redirects_to_private_addresses_or_social_sites_never_fetch_target(self):
        for target in ("http://127.0.0.1/private", "https://facebook.com/post/1"):
            with patch("modules.websites._get_page", return_value=(302, {"location": target}, b"")) as get:
                rows = collect_websites([URL])
                self.assertEqual(get.call_count, 1)
                self.assertEqual(rows[0]["source_type"], "batch_warning")
        # Public-looking names that resolve privately are checked on the next hop too.
        with patch("modules.websites._get_page", side_effect=[(302, {"location": "https://private.example/"}, b""),
                                                             WebsiteError("private address")]):
            self.assertEqual(collect_websites([URL])[0]["source_type"], "batch_warning")

    def test_failed_page_does_not_discard_earlier_or_later_results(self):
        with patch("modules.websites._get_page", side_effect=[page(), (403, {}, b""), page()]):
            rows = collect_websites([URL, URL + "2", URL + "3"])
        self.assertEqual([r["source_type"] for r in rows], ["website_page", "batch_warning", "website_page"])

    def test_redirect_loop_and_budget_are_bounded(self):
        with patch("modules.websites._get_page", return_value=(301, {"location": "/loop"}, b"")) as get:
            rows = collect_websites([URL])
            self.assertEqual(get.call_count, 6)
            self.assertIn("redirect", rows[0]["raw_text"])
        with patch("modules.websites.time.monotonic", side_effect=[0, 100, 100]), patch("modules.websites._get_page") as get:
            rows = collect_websites([URL, URL + "2"])
            get.assert_not_called()
            self.assertEqual(len(rows), 1)
            self.assertIn("Remaining links", rows[0]["raw_text"])


class IntegrationTests(unittest.TestCase):
    def record(self):
        with patch("modules.websites._get_page", return_value=page()):
            return collect_websites([URL])[0]

    def test_storage_preserves_page_metadata_and_only_reanalyzes_changed_text(self):
        from modules.database import upsert_post
        from modules.engagement import checks_for
        row = self.record()
        for previous in (None, {"raw_text": row["raw_text"], "status": "processed"},
                         {"raw_text": "old", "status": "processed"}):
            db = MagicMock()
            ref = db.collection.return_value.document.return_value
            ref.get.return_value.exists = previous is not None
            ref.get.return_value.to_dict.return_value = previous or {}
            upsert_post(db, row)
            saved = ref.set.call_args.args[0]
            self.assertEqual(saved["title"], "Product update")
            self.assertEqual(saved["platform"], "website")
            self.assertNotIn("checks", saved)
            self.assertEqual(checks_for(saved), [])
            if previous and previous["raw_text"] != row["raw_text"]:
                self.assertEqual(saved["analysis"], firestore.DELETE_FIELD)
                self.assertEqual(saved["status"], "pending")
            elif previous:
                self.assertNotIn("analysis", saved)
                self.assertEqual(saved["status"], "processed")

    def test_saved_pages_are_available_for_analysis_content_plans_and_reports(self):
        from modules.analysis_library import select_records
        from modules.client_report import content_type, report_record, velocity_view
        from modules.content_recommendations import source_records
        row = {**self.record(), "_id": "id1", "status": "processed", "analysis": {"sentiment": "Anger"}}
        self.assertEqual(select_records([row], source_urls=[], limit=10), [row])
        self.assertEqual(len(source_records([row])), 1)
        self.assertEqual(content_type(row), "Web page")
        self.assertEqual(velocity_view([report_record(row)])["rows"], [])

    def test_ui_validates_then_collects_previews_and_saves_only_page_records(self):
        script = "from dashboard import _render_website_workspace\n_render_website_workspace()"
        with patch("dashboard.collect_websites") as collect, patch("dashboard._write_results_to_firestore") as save:
            save.return_value = {"inserted": 1, "skipped": 0}
            app = AppTest.from_string(script).run()
            app.text_area(key="website_links").set_value("https://facebook.com/post/1").run()
            app.button(key="collect_website_pages").click().run()
            self.assertTrue(app.error)
            collect.assert_not_called()
            row = self.record()
            collect.return_value = [row, {"source_type": "batch_warning", "raw_text": "Unavailable"}]
            app.text_area(key="website_links").set_value(URL).run()
            app.text_input(key="website_custom_title").set_value("Product research").run()
            app.button(key="collect_website_pages").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.warning), 1)
            save.assert_called_once_with([row], custom_title="Product research")
            self.assertTrue(any("Saved 1 record" in notice.value for notice in app.success))
            self.assertFalse(list(app.exception))


if __name__ == "__main__":
    unittest.main()
