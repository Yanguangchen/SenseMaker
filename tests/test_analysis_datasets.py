"""Dataset boundaries must be enforced before record limits and paid AI calls."""

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.analysis_library import dataset_key, dataset_options, select_records
from modules.facebook_session import FacebookSession


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


if __name__ == "__main__":
    unittest.main()
