"""Dataset boundaries must be enforced before record limits and paid AI calls."""

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.analysis_library import dataset_key, dataset_options, select_records
from modules.facebook_session import FacebookSession
from modules.recent_collection import collection_snapshot


def record(ident, title="Campaign A", **extra):
    return {"_id": ident, "custom_title": title, "url": f"https://example.com/{ident}",
            "source_type": "website_page", "raw_text": f"Feedback from {ident}", "status": "pending",
            "scraped_at": "2026-11-01T00:00:00Z", **extra}


class DatasetSelectionTests(unittest.TestCase):
    def test_groups_titles_and_keeps_untitled_distinct_without_migration(self):
        rows = [record("a"), record("b", status="processed"), record("c", "Campaign B"), record("d", ""),
                record("e", "Untitled (older records)")]
        groups = {r["key"]: r for r in dataset_options(rows, None)}
        self.assertEqual(groups["title:Campaign A"]["count"], 2)
        self.assertEqual(groups["title:Campaign A"]["pending"], 1)
        self.assertIn("untitled:", groups)
        self.assertIn("title:Untitled (older records)", groups)
        self.assertEqual(dataset_key(record("a", " Campaign A ")), "title:Campaign A")

    def test_dataset_and_sources_filter_before_limit_and_unknown_dataset_is_empty(self):
        rows = [record(str(i), "Unrelated", scraped_at="2026-11-02T00:00:00Z") for i in range(200)]
        rows += [record("wanted"), record("old", source_type="container_post",
                                        url="https://facebook.com/groups/old/posts/1")]
        self.assertEqual([r["_id"] for r in select_records(rows, source_urls=[], limit=1, dataset="title:Campaign A")], ["wanted"])
        self.assertEqual(select_records(rows, source_urls=None, limit=10, dataset="title:Missing"), [])
        self.assertEqual(dataset_options([rows[-1]], []), [])

    def test_firestore_loader_uses_real_ids_and_filters_selected_dataset(self):
        from dashboard import _fetch_firestore_posts, _fetch_analysis_datasets
        db = MagicMock()
        rows = [record("a"), record("b", "Campaign B")]
        docs = [SimpleNamespace(id="doc-" + row["_id"], to_dict=lambda row=row: row) for row in rows]
        db.collection.return_value.where.return_value.stream.return_value = docs
        db.collection.return_value.select.return_value.stream.return_value = docs
        with patch("dashboard.init_db", return_value=db):
            found = _fetch_firestore_posts("pending", 10, None, "title:Campaign A")
            self.assertEqual([r["_id"] for r in found], ["doc-a"])
            self.assertEqual(len(_fetch_analysis_datasets(None)), 2)
        self.assertNotIn("raw_text", db.collection.return_value.select.call_args.args[0])


class DatasetUITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.rows = [record("a1"), record("a2"), record("b1", "Campaign B"), record("old", "")]
        self.stack.enter_context(patch("dashboard.require_access", return_value="editor@example.com"))
        self.stack.enter_context(patch("dashboard._get_facebook_session", return_value=FacebookSession()))
        self.stack.enter_context(patch("dashboard.IS_CLOUD", False))
        for name in ("_render_collection_workspace", "_render_news_workspace", "_render_x_workspace",
                     "_render_website_workspace", "_render_sentiment_history", "_render_news_risk_history", "render_gencite_workspace"):
            self.stack.enter_context(patch("dashboard." + name))
        self.stack.enter_context(patch("dashboard._render_analysis_sources", return_value=(None, True)))
        self.stack.enter_context(patch("dashboard._render_ai_settings", return_value="openai"))
        self.stack.enter_context(patch("dashboard.get_ai_key", return_value="test-key"))
        self.catalog = self.stack.enter_context(patch("dashboard._fetch_analysis_datasets",
                                                     side_effect=lambda sources: dataset_options(self.rows, sources)))
        self.fetch = self.stack.enter_context(patch("dashboard._fetch_firestore_posts", side_effect=lambda status_filter, limit, source_urls, dataset:
            select_records([row for row in self.rows if status_filter == "all" or row["status"] == status_filter],
                           source_urls=source_urls, limit=limit, dataset=dataset)))
        self.process = self.stack.enter_context(patch("dashboard._process_selected_firestore_posts",
            return_value={"processed": 1, "error": 0, "analyses": [], "errors": []}))
        self.app = AppTest.from_string("from dashboard import main\nmain()", default_timeout=30).run()
        self.assertFalse(list(self.app.exception))

    def load(self, key="title:Campaign A"):
        self.app.selectbox(key="analysis_dataset").select(key).run()
        self.app.button(key="load_firestore_records_btn").click().run()
        self.assertFalse(list(self.app.exception))

    def test_nothing_selected_or_sent_to_provider_until_explicit_choice(self):
        self.assertIsNone(self.app.selectbox(key="analysis_dataset").value)
        self.assertTrue(self.app.button(key="load_firestore_records_btn").disabled)
        self.assertTrue(self.app.button(key="load_processed_records_btn").disabled)
        self.load()
        self.assertEqual(self.app.multiselect(key="analysis_record_selection").value, [])
        self.assertTrue(self.app.button(key="run_analysis_selected_btn").disabled)
        self.process.assert_not_called()
        self.app.multiselect(key="analysis_record_selection").select("a2").run()
        self.assertEqual(self.app.button(key="run_analysis_selected_btn").label, "Run ChatGPT on 1 selected record")
        self.app.button(key="run_analysis_selected_btn").click().run()
        self.assertEqual([row["_id"] for row in self.process.call_args.args[0]], ["a2"])

    def test_select_all_is_bounded_to_dataset_and_clear_disables_run(self):
        self.load()
        self.app.session_state["firestore_rows"].append(record("foreign", "Campaign B"))
        self.app.run()
        self.app.button(key="analysis_select_all").click().run()
        self.assertEqual(set(self.app.multiselect(key="analysis_record_selection").value), {"a1", "a2"})
        self.app.button(key="run_analysis_selected_btn").click().run()
        self.assertEqual({r["_id"] for r in self.process.call_args.args[0]}, {"a1", "a2"})
        self.app.button(key="analysis_clear_selection").click().run()
        self.assertTrue(self.app.button(key="run_analysis_selected_btn").disabled)

    def test_switching_datasets_clears_selection_results_and_content_plan(self):
        self.load()
        self.app.button(key="analysis_select_all").click().run()
        self.app.session_state["analysis_last_results"] = {"processed": 2, "analyses": []}
        self.app.session_state["processed_rows"] = [record("a1", status="processed")]
        self.app.session_state["content_recommendation_plan"] = {"old": True}
        self.app.selectbox(key="analysis_dataset").select("title:Campaign B").run()
        for key in ("firestore_rows", "processed_rows", "analysis_record_selection", "analysis_last_results", "content_recommendation_plan"):
            self.assertNotIn(key, self.app.session_state)
        self.app.button(key="load_firestore_records_btn").click().run()
        self.assertEqual([r["_id"] for r in self.app.session_state["firestore_rows"]], ["b1"])
        self.assertEqual(self.app.multiselect(key="analysis_record_selection").value, [])

    def test_status_limit_reload_and_refresh_reset_selection(self):
        self.load()
        self.app.button(key="analysis_select_all").click().run()
        self.app.selectbox(key="analysis_record_status").select("all").run()
        self.assertNotIn("firestore_rows", self.app.session_state)
        self.app.button(key="load_firestore_records_btn").click().run()
        self.app.button(key="analysis_select_all").click().run()
        self.app.slider(key="analysis_load_limit").set_value(1).run()
        self.assertNotIn("firestore_rows", self.app.session_state)
        self.app.button(key="load_firestore_records_btn").click().run()
        self.app.button(key="analysis_select_all").click().run()
        self.app.button(key="load_firestore_records_btn").click().run()
        self.assertEqual(self.app.multiselect(key="analysis_record_selection").value, [])
        self.app.button(key="refresh_analysis_datasets").click().run()
        self.assertNotIn("firestore_rows", self.app.session_state)

    def test_untitled_records_remain_accessible_and_processed_loading_is_scoped(self):
        self.rows[-1]["status"] = "processed"
        self.app.selectbox(key="analysis_dataset").select("untitled:").run()
        self.app.button(key="load_processed_records_btn").click().run()
        self.assertEqual([r["_id"] for r in self.app.session_state["processed_rows"]], ["old"])
        self.assertEqual(self.fetch.call_args.kwargs["dataset"], "untitled:")

    def test_failed_refresh_cannot_keep_old_records_runnable(self):
        self.load()
        self.app.button(key="analysis_select_all").click().run()
        self.catalog.side_effect = RuntimeError("offline")
        self.app.button(key="refresh_analysis_datasets").click().run()
        self.assertTrue(self.app.error)
        self.assertIsNone(self.app.selectbox(key="analysis_dataset").value)
        self.assertTrue(self.app.button(key="load_firestore_records_btn").disabled)
        self.assertNotIn("firestore_rows", self.app.session_state)

    def use_latest(self, rows=None, saved=True):
        recent = collection_snapshot(self.rows[:2] if rows is None else rows, "Custom website", 100)
        recent["saved"] = saved
        self.app.session_state["last_collected_dataset"] = recent
        self.app.radio(key="analysis_selection_mode").set_value("Last scraped data").run()
        return recent

    def test_latest_is_exact_batch_even_with_reused_title_and_no_source_library(self):
        recent = self.use_latest(rows=[self.rows[0]])
        with patch("dashboard._fetch_last_scrape_posts", return_value=recent["rows"] + [self.rows[1]]) as fetch:
            self.app.button(key="load_firestore_records_btn").click().run()
            self.assertFalse(self.app.exception)
            self.assertEqual(self.app.selectbox(key="analysis_recent_status").value, "all")
            self.app.button(key="analysis_select_all").click().run()
            self.app.button(key="run_analysis_selected_btn").click().run()
            self.assertEqual([r["_id"] for r in self.process.call_args.args[0]], [recent["rows"][0]["_id"]])
            self.assertEqual(fetch.call_args.kwargs["status_filter"], "all")
            self.fetch.assert_not_called()

    def test_unsaved_latest_requires_successful_save_before_loading(self):
        recent = self.use_latest(saved=False)
        with patch("dashboard._write_results_to_firestore", side_effect=RuntimeError("offline")) as save, \
                patch("dashboard._fetch_last_scrape_posts", return_value=recent["rows"]) as fetch:
            self.assertEqual(self.app.button(key="load_firestore_records_btn").label, "Save and load last scraped data")
            self.app.button(key="load_firestore_records_btn").click().run()
            self.assertTrue(self.app.error)
            fetch.assert_not_called()
            self.process.assert_not_called()
            save.side_effect = None
            self.app.button(key="load_firestore_records_btn").click().run()
            self.assertEqual(save.call_args.args[0], recent["rows"])
            fetch.assert_called_once()
            self.assertEqual(self.app.multiselect(key="analysis_record_selection").value, [])

    def test_mode_change_and_new_latest_clear_prior_selection_results_and_plan(self):
        self.load()
        self.app.button(key="analysis_select_all").click().run()
        self.app.session_state["analysis_last_results"] = {"analyses": []}
        self.app.session_state["content_recommendation_plan"] = {"old": True}
        recent = self.use_latest()
        for key in ("firestore_rows", "analysis_record_selection", "analysis_last_results", "content_recommendation_plan"):
            self.assertNotIn(key, self.app.session_state)
        with patch("dashboard._fetch_last_scrape_posts", return_value=recent["rows"]):
            self.app.button(key="load_firestore_records_btn").click().run()
            self.app.button(key="analysis_select_all").click().run()
            # Same title and URLs, a new collection must still invalidate loaded data.
            self.app.session_state["last_collected_dataset"] = collection_snapshot(self.rows[:2], "Custom website", 100)
            self.app.run()
            self.assertNotIn("firestore_rows", self.app.session_state)
            self.assertNotIn("analysis_record_selection", self.app.session_state)
        self.app.radio(key="analysis_selection_mode").set_value("Manual selection").run()
        self.assertNotIn("firestore_rows", self.app.session_state)

    def test_no_recent_or_empty_recent_never_falls_back_to_saved_library(self):
        self.app.radio(key="analysis_selection_mode").set_value("Last scraped data").run()
        self.assertTrue(self.app.button(key="load_firestore_records_btn").disabled)
        self.assertTrue(self.app.button(key="load_processed_records_btn").disabled)
        self.use_latest(rows=[])
        self.assertTrue(self.app.button(key="load_firestore_records_btn").disabled)
        self.fetch.assert_not_called()
        self.process.assert_not_called()

    def test_latest_processed_view_uses_membership_not_title(self):
        recent = self.use_latest(rows=[self.rows[0]])
        with patch("dashboard._fetch_last_scrape_posts", return_value=[]) as fetch:
            self.app.button(key="load_processed_records_btn").click().run()
            self.assertEqual(fetch.call_args.args[0]["id"], recent["id"])
            self.assertEqual(fetch.call_args.kwargs["status_filter"], "processed")
            self.fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
