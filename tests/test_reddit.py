"""API pagination, evidence identity, errors and persistence for Reddit ingestion."""

import asyncio
import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from firebase_admin import firestore

from modules.database import mark_post_processed, upsert_post
from modules.reddit import CAMBODIA_URL, RedditClient, RedditError, collect_reddit, parse_reddit_source


def post(ident="a", **kwargs):
    return {"kind": "t3", "data": {"id": ident, "subreddit": "cambodia", "title": "Thai project discussion",
                                   "selftext": "An update", "author": "do-not-store-me", "created_utc": 1700000000, **kwargs}}


def listing(children, after=None):
    return {"kind": "Listing", "data": {"children": children, "after": after}}


def comment(ident="c", **kwargs):
    return {"kind": "t1", "data": {"id": ident, "subreddit": "cambodia", "body": "I disagree with a boycott.",
                                   "author": "also-not-stored", "created_utc": 1700000001, **kwargs}}


class RedditTests(unittest.TestCase):
    def test_real_transport_uses_oauth_and_respects_rate_limit_errors(self):
        values = {"REDDIT_CLIENT_ID": "client", "REDDIT_CLIENT_SECRET": "secret", "REDDIT_USER_AGENT": "test-agent"}
        token = MagicMock()
        token.__enter__.return_value = io.StringIO(json.dumps({"access_token": "test-token"}))
        token.__enter__.return_value.headers = {}
        feed = MagicMock()
        feed.__enter__.return_value = io.StringIO(json.dumps(listing([])))
        feed.__enter__.return_value.headers = {}
        with patch("modules.reddit.get_setting", side_effect=lambda k: values.get(k, "")), \
             patch("modules.reddit._next_request", 0), patch("modules.reddit.time.sleep"), \
             patch("modules.reddit.urlopen", side_effect=[token, feed]) as request:
            client = RedditClient(float("inf"))
            self.assertEqual(client.get("/r/cambodia/new", {}), listing([]))
            self.assertIn("api/v1/access_token", request.call_args_list[0].args[0].full_url)
            self.assertEqual(request.call_args_list[1].args[0].get_header("Authorization"), "Bearer test-token")
            self.assertTrue(request.call_args_list[1].args[0].full_url.startswith("https://oauth.reddit.com/"))
        with patch("modules.reddit._next_request", 0), patch("modules.reddit.urlopen", side_effect=HTTPError(
                "https://oauth.reddit.com", 429, "Too many", {"Retry-After": "60"}, None)) as request:
            with self.assertRaisesRegex(RedditError, "rate limit"):
                client.get("/r/cambodia/new", {})
            self.assertEqual(request.call_count, 1)

    def test_supports_default_search_and_thread_sources_and_rejects_other_hosts(self):
        self.assertEqual(parse_reddit_source(CAMBODIA_URL)[0], "/r/cambodia/new")
        path, params, _ = parse_reddit_source(CAMBODIA_URL + "search/?q=Thai%20project&t=week")
        self.assertEqual(params["restrict_sr"], "on")
        self.assertEqual(params["q"], "Thai project")
        self.assertEqual(parse_reddit_source(CAMBODIA_URL + "comments/abc/title/")[2], "abc")
        for url in ("https://reddit.com.evil/r/cambodia", "https://www.reddit.com/r/other/",
                    "https://user:pass@reddit.com/r/cambodia", "https://reddit.com/r/cambodia/wiki"):
            with self.assertRaises(RedditError):
                parse_reddit_source(url)

    def test_paginates_beyond_100_and_deduplicates(self):
        client = MagicMock()
        client.get.side_effect = [listing([post(f"a{i}") for i in range(100)], "t3_a99"),
                                  listing([post("a99")] + [post(f"a{i}") for i in range(100, 200)], "t3_a199"),
                                  listing([post(f"a{i}") for i in range(200, 205)])]
        rows = collect_reddit(CAMBODIA_URL, max_posts=205, comments_per_post=0, client=client)
        self.assertEqual(len(rows), 205)
        self.assertEqual(client.get.call_args_list[1].args[1]["after"], "t3_a99")
        self.assertEqual(len({r["url"] for r in rows}), 205)
        self.assertNotIn("author", rows[0])

    def test_comments_are_separate_with_parent_context_and_stable_permalinks(self):
        client = MagicMock()
        client.get.return_value = [listing([post()]), listing([
            comment(replies=listing([comment("d", body="Another reply")])),
            {"kind": "more", "data": {"children": ["not-fetched"]}},
        ])]
        rows = collect_reddit(CAMBODIA_URL + "comments/a/title/", comments_per_post=10, client=client)
        self.assertEqual([r["source_type"] for r in rows], ["reddit_post", "reddit_comment", "reddit_comment"])
        self.assertEqual(rows[0]["comment_count"], 2)
        self.assertEqual(rows[1]["raw_text"], "I disagree with a boycott.")
        self.assertEqual(rows[1]["parent_text"], rows[0]["raw_text"])
        self.assertTrue(rows[1]["url"].endswith("/a/_/c/"))
        self.assertNotIn("author", rows[1])

    def test_partial_api_failure_keeps_records_and_is_not_an_empty_success(self):
        client = MagicMock()
        client.get.side_effect = [listing([post()], "next"), RedditError("Rate limited")]
        rows = collect_reddit(CAMBODIA_URL, comments_per_post=0, client=client)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["source_type"], "batch_warning")
        client.get.side_effect = RedditError("Access denied")
        with self.assertRaisesRegex(RedditError, "Access denied"):
            collect_reddit(CAMBODIA_URL, client=client)

    def test_removed_content_becomes_a_tombstone_and_is_not_analyzed(self):
        client = MagicMock()
        client.get.return_value = listing([post(selftext="[removed]")])
        row = collect_reddit(CAMBODIA_URL, client=client)[0]
        self.assertTrue(row["content_removed"])
        self.assertEqual(row["raw_text"], "")
        self.assertEqual(client.get.call_count, 1)

    def test_missing_credentials_never_falls_back_to_browser_scraping(self):
        from modules.scraper import scrape_group
        with patch("modules.reddit.reddit_configured", return_value=False), patch("modules.scraper.async_playwright") as browser:
            with self.assertRaisesRegex(RedditError, "not connected"):
                asyncio.run(scrape_group(CAMBODIA_URL))
            browser.assert_not_called()


class RedditPersistenceTests(unittest.TestCase):
    def db(self, previous=None):
        db = MagicMock()
        ref = db.collection.return_value.document.return_value
        ref.get.return_value.exists = previous is not None
        ref.get.return_value.to_dict.return_value = previous or {}
        return db, ref

    def test_preserves_reddit_context_and_invalidates_changed_analysis(self):
        db, ref = self.db({"raw_text": "Old text", "status": "processed", "analysis": {"old": True}})
        upsert_post(db, {"url": "https://reddit.com/r/cambodia/comments/a/", "raw_text": "New text",
                         "source_type": "reddit_comment", "parent_text": "Thai project", "platform": "reddit"})
        payload = ref.set.call_args.args[0]
        self.assertEqual(payload["parent_text"], "Thai project")
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["analysis"], firestore.DELETE_FIELD)

    def test_seen_deleted_content_clears_saved_text_context_and_analysis(self):
        db, ref = self.db({"raw_text": "Old text", "status": "processed"})
        upsert_post(db, {"url": "https://reddit.com/r/cambodia/comments/a/", "raw_text": "",
                         "source_type": "reddit_post", "content_removed": True})
        payload = ref.set.call_args.args[0]
        self.assertEqual(payload["status"], "removed")
        self.assertEqual(payload["analysis"], firestore.DELETE_FIELD)
        self.assertEqual(payload["parent_text"], firestore.DELETE_FIELD)

    def test_error_records_cannot_be_saved_as_posts_and_failed_analysis_clears_old_result(self):
        db, ref = self.db()
        with self.assertRaises(ValueError):
            upsert_post(db, {"url": CAMBODIA_URL, "source_type": "batch_warning"})
        mark_post_processed(db, "a", None, "error")
        self.assertEqual(ref.set.call_args.args[0]["analysis"], firestore.DELETE_FIELD)


if __name__ == "__main__":
    unittest.main()
