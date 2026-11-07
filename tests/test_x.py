"""Recent-search collection, cost bound, identity handling and persistence for X."""

import io
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from firebase_admin import firestore
from streamlit.testing.v1 import AppTest

from modules.x import (
    XClient, XError, X_SEARCHES, build_post_query, collect_x, estimate_reads, search_url,
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


class XCollectionFlowTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.connected = stack.enter_context(patch("dashboard.x_configured", return_value=True))
        self.collect = stack.enter_context(patch("dashboard.collect_x", return_value=[{
            "url": "https://x.com/i/status/1", "raw_text": "Collected post", "source_type": "x_post",
        }]))
        self.client = stack.enter_context(patch("dashboard.XClient", return_value=SimpleNamespace(reads=1)))
        self.save = stack.enter_context(patch("dashboard._write_results_to_firestore",
                                              return_value={"inserted": 1, "skipped": 0}))
        self.app = AppTest.from_string(
            "import streamlit as st\n"
            "from dashboard import _render_x_workspace, _close_x_collection_dialog\n"
            "if st.session_state.pop('test_dismiss', False):\n"
            "    _close_x_collection_dialog()\n"
            "_render_x_workspace()"
        ).run()
        self.assertFalse(self.app.text_input)
        self.app.button(key="open_x_collection").click().run()
        self.assertEqual(len(self.app.get("dialog")), 1)

    def button_keys(self):
        return {button.key for button in self.app.button}

    def test_options_open_immediately_and_names_are_generated_only_when_collecting(self):
        self.assertEqual(len(self.app.checkbox), len(X_SEARCHES))
        self.assertEqual(len(self.app.number_input), 2)
        self.assertNotIn("x_custom_title_value", [field.key for field in self.app.text_input])
        self.assertNotIn("x_name_next", self.button_keys())
        self.assertNotIn("x_options_back", self.button_keys())
        self.assertNotIn("collect_x_posts", self.button_keys())
        self.app.session_state["x_dataset_name"] = "Legacy custom name"
        self.app.session_state["x_custom_title_value"] = "Legacy custom name"
        self.app.button(key="x_options_next").click().run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.text_input)
        self.assertFalse(self.app.checkbox)
        self.assertFalse(self.app.number_input)
        self.assertFalse(self.app.button(key="collect_x_posts").disabled)
        self.client.assert_not_called()
        self.app.button(key="collect_x_posts").click().run()
        self.assertTrue(self.save.call_args.kwargs["custom_title"].startswith("X · "))
        self.assertNotEqual(self.save.call_args.kwargs["custom_title"], "Legacy custom name")

    def test_no_search_terms_cannot_advance(self):
        for key in X_SEARCHES:
            self.app.checkbox(key=f"x_enabled_{key}").uncheck()
        self.app.run()
        self.assertTrue(self.app.button(key="x_options_next").disabled)
        self.app.checkbox(key="x_enabled_oyoshi").check().run()
        self.app.text_input(key="x_query_oyoshi").set_value(" ").run()
        self.assertTrue(self.app.button(key="x_options_next").disabled)
        self.assertNotIn("collect_x_posts", self.button_keys())
        self.collect.assert_not_called()

    def test_back_navigation_preserves_options_and_only_final_action_collects(self):
        self.app.text_input(key="x_query_oyoshi").set_value("  OYOSHI feedback  ")
        self.app.checkbox(key="x_enabled_border_tension").uncheck()
        self.app.checkbox(key="x_enabled_boycott").uncheck()
        self.app.number_input(key="x_post_limit").set_value(20)
        self.app.number_input(key="x_reply_limit").set_value(2)
        self.app.run()
        self.app.button(key="x_options_next").click().run()
        self.app.run()
        self.app.button(key="x_collect_back").click().run()
        self.assertEqual(self.app.text_input(key="x_query_oyoshi").value, "  OYOSHI feedback  ")
        self.assertFalse(self.app.checkbox(key="x_enabled_border_tension").value)
        self.assertEqual(self.app.number_input(key="x_post_limit").value, 20)
        self.assertEqual(self.app.number_input(key="x_reply_limit").value, 2)
        self.app.button(key="x_options_next").click().run()
        self.collect.assert_not_called()
        self.app.button(key="collect_x_posts").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.collect.call_args.args[0], {"oyoshi": "OYOSHI feedback"})
        self.assertEqual(self.collect.call_args.kwargs["max_posts"], 20)
        self.assertEqual(self.collect.call_args.kwargs["replies_per_post"], 2)
        self.assertTrue(self.save.call_args.kwargs["custom_title"].startswith("X · "))
        self.app.run()
        self.assertEqual(self.collect.call_count, 1)
        self.assertEqual(self.save.call_count, 1)

    def test_missing_connection_disables_final_action(self):
        self.connected.return_value = False
        self.app.button(key="x_options_next").click().run()
        self.assertTrue(self.app.button(key="collect_x_posts").disabled)
        self.assertTrue(any("not connected" in item.value for item in self.app.info))
        self.client.assert_not_called()

    def test_failed_save_is_recoverable_while_editing_an_earlier_step(self):
        self.save.side_effect = [RuntimeError("offline"), {"inserted": 1, "skipped": 0}]
        self.app.button(key="x_options_next").click().run()
        self.app.button(key="collect_x_posts").click().run()
        self.assertTrue(self.app.button(key="collect_x_posts").disabled)
        self.app.button(key="x_collect_back").click().run()
        title = self.save.call_args.kwargs["custom_title"]
        self.app.text_input(key="x_query_oyoshi").set_value("Next search").run()
        self.app.button(key="retry_x_save_dialog").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.save.call_args.kwargs["custom_title"], title)
        self.assertEqual(self.app.session_state["x_collection"]["cloud_save"]["state"], "saved")
        self.assertEqual(self.collect.call_count, 1)
        self.assertFalse(self.app.session_state["x_focus_open"])

    def dismiss(self):
        self.app.session_state["test_dismiss"] = True
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.session_state["x_focus_open"])

    def test_close_and_reopen_keeps_options_and_current_step(self):
        self.app.text_input(key="x_query_oyoshi").set_value("custom query")
        self.app.checkbox(key="x_enabled_boycott").uncheck()
        self.app.number_input(key="x_post_limit").set_value(30)
        self.app.number_input(key="x_reply_limit").set_value(1)
        self.app.run()
        self.dismiss()
        self.app.run()
        self.app.button(key="open_x_collection").click().run()
        self.assertEqual(self.app.text_input(key="x_query_oyoshi").value, "custom query")
        self.assertFalse(self.app.checkbox(key="x_enabled_boycott").value)
        self.assertEqual(self.app.number_input(key="x_post_limit").value, 30)
        self.assertEqual(self.app.number_input(key="x_reply_limit").value, 1)
        self.assertEqual(self.app.session_state["x_collection_step"], 2)
        self.collect.assert_not_called()
        self.save.assert_not_called()

    def test_success_closes_dialog_and_reopen_does_not_collect_again(self):
        self.app.button(key="x_options_next").click().run()
        self.app.button(key="collect_x_posts").click().run()
        self.assertFalse(self.app.session_state["x_focus_open"])
        self.assertTrue(any("Saved 1 record" in notice.value for notice in self.app.success))
        # AppTest 1.50 retains closed dialog widgets after st.rerun(). Refresh
        # without their stale triggers, as the browser does when a modal closes.
        self.app._run()
        self.app.button(key="open_x_collection").click().run()
        self.assertIn("collect_x_posts", self.button_keys())
        self.assertEqual(self.collect.call_count, 1)

    def test_opening_x_clears_the_previous_workspace_modal(self):
        self.dismiss()
        self.app.session_state["focus_workspace_modal"] = "website"
        self.app.button(key="open_x_collection").click().run()
        self.assertTrue(self.app.session_state["x_focus_open"])
        self.assertNotIn("focus_workspace_modal", self.app.session_state)

    def test_dialog_rechecks_the_signed_in_user_before_rendering(self):
        app = AppTest.from_string(
            "from dashboard import _render_x_workspace\n_render_x_workspace('editor@example.com')"
        ).run()
        app.session_state["auth_token"] = "expired-token"
        with patch("modules.auth.allowed_emails", return_value={"editor@example.com"}), \
                patch("modules.auth.verify_access", side_effect=PermissionError("expired")) as verify:
            app.button(key="open_x_collection").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.text_input)
        self.assertFalse(app.session_state["x_focus_open"])
        verify.assert_called_once_with("expired-token", {"editor@example.com"})
        self.collect.assert_not_called()


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
