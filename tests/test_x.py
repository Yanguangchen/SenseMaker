"""Recent-search collection, cost bound, identity handling and persistence for X."""

import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from firebase_admin import firestore

from modules.x import (
    XClient, XError, build_post_query, collect_x, estimate_reads, search_url,
)


def tweet(ident="1", text="Thai shops are closing", **extra):
    return {
        "id": str(ident), "text": text, "created_at": "2026-11-01T00:00:00.000Z",
        "conversation_id": str(ident), "lang": "en", "author_id": "do-not-store",
        "public_metrics": {"reply_count": 4, "like_count": 9, "impression_count": 100,
                           "retweet_count": 2},
        **extra,
    }


def page(tweets, token=None, includes=None):
    payload = {"data": tweets, "meta": {"result_count": len(tweets)}}
    if token:
        payload["meta"]["next_token"] = token
    if includes:
        payload["includes"] = {"tweets": includes}
    return payload


class XCollectionTests(unittest.TestCase):
    def test_transport_uses_bearer_token_and_does_not_echo_it_on_failure(self):
        body = MagicMock()
        body.__enter__.return_value = io.StringIO(json.dumps(page([])))
        body.__enter__.return_value.headers = {}
        with patch("modules.x.get_setting", return_value="secret-token"), \
             patch("modules.x._next_request", 0), patch("modules.x.time.sleep"), \
             patch("modules.x.urlopen", return_value=body) as request:
            client = XClient(float("inf"))
            self.assertEqual(client.search("OYOSHI", 10), page([]))
            sent = request.call_args.args[0]
            self.assertTrue(sent.full_url.startswith("https://api.x.com/2/tweets/search/recent?"))
            self.assertEqual(sent.get_header("Authorization"), "Bearer secret-token")
            self.assertNotIn("secret-token", sent.full_url)
        with patch("modules.x._next_request", 0), patch("modules.x.urlopen", side_effect=HTTPError(
                "https://api.x.com/2/tweets/search/recent", 401, "Unauthorized", {}, None)):
            with self.assertRaises(XError) as caught:
                client.search("OYOSHI", 10)
            self.assertTrue(caught.exception.fatal)
            self.assertNotIn("secret-token", str(caught.exception))

    def test_query_adds_repost_and_reply_filters_and_rejects_an_empty_search(self):
        self.assertEqual(build_post_query("OYOSHI"), "OYOSHI -is:retweet -is:reply")
        self.assertEqual(build_post_query("OYOSHI -is:retweet -is:reply"), "OYOSHI -is:retweet -is:reply")
        with self.assertRaises(XError):
            build_post_query("  ")
        self.assertEqual(estimate_reads(3, 10, 0), 30)
        self.assertEqual(estimate_reads(1, 10, 4), 210)

    def test_paginates_deduplicates_and_drops_identity_and_view_counts(self):
        client = MagicMock()
        client.search.side_effect = [
            page([tweet(i) for i in range(1, 7)], token="next"),
            page([tweet(6), *[tweet(i) for i in range(7, 12)]]),
        ]
        rows = collect_x({"oyoshi": "OYOSHI"}, max_posts=10, replies_per_post=0, client=client)
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({row["x_id"] for row in rows}), 10)
        self.assertEqual(rows[0]["url"], "https://x.com/i/status/1")
        self.assertEqual(rows[0]["reported_comment_count"], 4)
        self.assertNotIn("author_id", rows[0])
        self.assertNotIn("impression_count", json.dumps(rows[0]))
        self.assertEqual(client.search.call_args_list[1].args[2], "next")

    def test_long_text_is_kept_and_reposts_are_skipped(self):
        client = MagicMock()
        client.search.return_value = page([
            tweet("1", text="short", note_tweet={"text": "The full boycott discussion " * 5}),
            tweet("2", text="RT", referenced_tweets=[{"type": "retweeted", "id": "9"}]),
        ])
        rows = collect_x({"oyoshi": "OYOSHI"}, client=client)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["raw_text"].startswith("The full boycott discussion"))

    def test_replies_are_separate_records_with_parent_context(self):
        client = MagicMock()
        client.search.side_effect = [
            page([tweet("10", text="Stop buying Thai goods")]),
            page([tweet("11", text="I disagree", conversation_id="10",
                       referenced_tweets=[{"type": "replied_to", "id": "10"}])],
                 includes=[tweet("10", text="Stop buying Thai goods")]),
        ]
        rows = collect_x({"boycott": "boycott"}, max_posts=10, replies_per_post=1, client=client)
        self.assertEqual([row["source_type"] for row in rows], ["x_post", "x_reply"])
        self.assertEqual(rows[1]["parent_text"], "Stop buying Thai goods")
        self.assertEqual(rows[1]["url"], "https://x.com/i/status/11")
        self.assertEqual(rows[0]["comment_count"], 1)
        self.assertNotIn("author_id", rows[1])
        self.assertNotIn("reported_comment_count", rows[1])

    def test_a_failed_search_keeps_earlier_posts_and_stops_when_access_is_denied(self):
        client = MagicMock()
        client.search.side_effect = [page([tweet("1")], token="next"), XError("Rate limited", fatal=True)]
        rows = collect_x({"oyoshi": "OYOSHI", "border_tension": "border"}, replies_per_post=0, client=client)
        self.assertEqual([row["source_type"] for row in rows], ["x_post", "batch_warning"])
        self.assertEqual(client.search.call_count, 2)

    def test_withheld_post_without_text_is_a_tombstone(self):
        client = MagicMock()
        client.search.return_value = page([tweet("5", text="", withheld={"country_codes": ["TH"]})])
        row = collect_x({"oyoshi": "OYOSHI"}, client=client)[0]
        self.assertTrue(row["content_removed"])
        self.assertEqual(row["raw_text"], "")

    def test_missing_token_never_calls_x_or_a_browser(self):
        with patch("modules.x.get_setting", return_value=""), patch("modules.x.urlopen") as request:
            with self.assertRaisesRegex(XError, "not connected"):
                collect_x({"oyoshi": "OYOSHI"})
            request.assert_not_called()
        self.assertTrue(search_url("OYOSHI").startswith("https://x.com/search?"))


class XPersistenceTests(unittest.TestCase):
    def db(self, previous=None):
        db = MagicMock()
        ref = db.collection.return_value.document.return_value
        ref.get.return_value.exists = previous is not None
        ref.get.return_value.to_dict.return_value = previous or {}
        return db, ref

    def test_reply_count_starts_a_velocity_check_and_edits_reset_analysis(self):
        from modules.database import upsert_post
        db, ref = self.db({"raw_text": "Old text", "status": "processed", "analysis": {"old": True}})
        upsert_post(db, {"url": "https://x.com/i/status/1", "raw_text": "New text", "source_type": "x_post",
                         "platform": "x", "x_id": "1", "reported_comment_count": 6,
                         "scraped_at": "2026-11-02T00:00:00+00:00"})
        payload = ref.set.call_args.args[0]
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["analysis"], firestore.DELETE_FIELD)
        self.assertEqual(payload["reported_comment_count"], 6)
        self.assertEqual(payload["checks"][0]["comments"], 6)

    def test_replies_are_saved_without_a_velocity_check(self):
        from modules.database import upsert_post
        db, ref = self.db()
        upsert_post(db, {"url": "https://x.com/i/status/2", "raw_text": "A reply", "source_type": "x_reply",
                         "parent_text": "Original post"})
        payload = ref.set.call_args.args[0]
        self.assertEqual(payload["parent_text"], "Original post")
        self.assertNotIn("checks", payload)

    def test_withheld_content_clears_saved_text_and_analysis(self):
        from modules.database import upsert_post
        db, ref = self.db({"raw_text": "Old text", "status": "processed", "analysis": {"old": True}})
        upsert_post(db, {"url": "https://x.com/i/status/3", "raw_text": "", "source_type": "x_post",
                         "content_removed": True})
        payload = ref.set.call_args.args[0]
        self.assertEqual(payload["status"], "removed")
        self.assertEqual(payload["raw_text"], "")
        self.assertEqual(payload["analysis"], firestore.DELETE_FIELD)


if __name__ == "__main__":
    unittest.main()
