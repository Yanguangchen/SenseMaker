"""Dataset boundaries must be enforced before record limits and paid AI calls."""

import unittest
from datetime import datetime, timezone
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.analysis_library import dataset_date_label, dataset_key, dataset_options, select_records
from modules.facebook_session import FacebookSession
from modules.recent_collection import collection_snapshot
from tests.analysis_steps import ui


def record(ident, title="Campaign A", **extra):
    return {"_id": ident, "custom_title": title, "url": f"https://example.com/{ident}",
            "source_type": "website_page", "raw_text": f"Feedback from {ident}", "status": "pending",
            "scraped_at": "2026-11-01T00:00:00Z", **extra}


class DatasetSelectionTests(unittest.TestCase):
    def test_dates_use_latest_save_then_collection_or_storage_not_publication(self):
        rows = [record("a", saved_at="2026-11-03T21:30:45+08:00"),
                record("b", saved_at="2026-11-02T12:00:00Z"),
                record("old", "Legacy", scraped_at="", created_at="2030-01-01T00:00:00Z",
                       record_created_at=datetime(2026, 10, 1, 4, tzinfo=timezone.utc)),
                record("collected", "Collected only"),
                record("undated", "Undated", scraped_at="", created_at="2030-01-01T00:00:00Z")]
        groups = dataset_options(rows, None)
        self.assertEqual(groups[0]["key"], "title:Campaign A")
        self.assertEqual(dataset_date_label(groups[0]), "03 Nov 2026, 13:30:45 UTC")
        by_key = {row["key"]: row for row in groups}
        self.assertEqual(dataset_date_label(by_key["title:Legacy"]), "01 Oct 2026, 04:00:00 UTC")
        self.assertEqual(dataset_date_label(by_key["title:Collected only"]), "01 Nov 2026, 00:00:00 UTC")
        self.assertEqual(dataset_date_label(by_key["title:Undated"]), "Date unavailable")

    def test_metadata_loader_recovers_firestore_creation_date_for_undated_records(self):
        from dashboard import _fetch_analysis_datasets
        db = MagicMock()
        db.collection.return_value.select.return_value.stream.return_value = [SimpleNamespace(
            create_time=datetime(2026, 10, 30, 4, 15, tzinfo=timezone.utc),
            to_dict=lambda: record("legacy", scraped_at="", created_at="2020-01-01T00:00:00Z"))]
        with patch("dashboard.init_db", return_value=db):
            groups = _fetch_analysis_datasets(["source:website"])
        self.assertEqual(dataset_date_label(groups[0]), "30 Oct 2026, 04:15:00 UTC")

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
        self.assertEqual([r["_id"] for r in select_records(rows, source_urls=["source:website"], limit=1, dataset="title:Campaign A")], ["wanted"])
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

    def test_multiple_datasets_form_a_union_before_the_shared_load_limit(self):
        rows = [record("unrelated", "Other", scraped_at="2026-11-03T00:00:00Z"),
                record("a"), record("b", "Campaign B", scraped_at="2026-11-02T00:00:00Z"),
                record("untitled", ""), record("old-source", "Campaign B", source_type="container_post",
                                             url="https://facebook.com/groups/old/posts/1")]
        selected = ["title:Campaign A", "title:Campaign B", "title:Campaign A"]
        self.assertEqual([r["_id"] for r in select_records(rows, source_urls=["source:website"], limit=2, dataset=selected)], ["b", "a"])
        self.assertEqual(len(select_records(rows, source_urls=["source:website"], limit=50, dataset=selected)), 2)
        self.assertEqual(select_records(rows, source_urls=None, limit=50, dataset=[]), [])
        self.assertEqual(select_records(rows, source_urls=None, limit=50, dataset=["title:Missing"]), [])
        self.assertEqual(len(select_records(rows, source_urls=["source:website"], limit=50, dataset=selected + ["untitled:"])), 3)

    def test_firestore_loader_applies_status_and_sources_to_multiple_datasets(self):
        from dashboard import _fetch_firestore_posts
        db = MagicMock()
        rows = [record("a"), record("b", "Campaign B"), record("other", "Other")]
        db.collection.return_value.where.return_value.stream.return_value = [
            SimpleNamespace(id="doc-" + row["_id"], to_dict=lambda row=row: row) for row in rows
        ]
        with patch("dashboard.init_db", return_value=db):
            found = _fetch_firestore_posts("pending", 2, ["source:website"], ["title:Campaign A", "title:Campaign B"])
        self.assertEqual({r["_id"] for r in found}, {"doc-a", "doc-b"})
        db.collection.return_value.where.assert_called_once_with("status", "==", "pending")


class SourceDatasetUITests(unittest.TestCase):
    def test_sources_start_empty_and_filter_dated_dataset_choices(self):
        rows = [record("website", "Mixed", saved_at="2026-11-03T12:00:00Z"),
                record("facebook", "Mixed", source_type="container_post",
                       url="https://facebook.com/groups/1/posts/2", saved_at="2026-11-02T09:30:00Z"),
                record("news", "News only", source_type="news_article")]
        script = ("from dashboard import _render_analysis_sources, _render_analysis_datasets\n"
                  "sources, ready = _render_analysis_sources('owner@example.com')\n"
                  "_render_analysis_datasets(sources, ready)\n")
        with patch("dashboard._fetch_analysis_datasets", side_effect=lambda sources: dataset_options(rows, sources)) as fetch:
            app = AppTest.from_string(script, default_timeout=30).run()
            self.assertEqual(ui(app, "analysis_sources").value, [])
            self.assertTrue(ui(app, "analysis_datasets").disabled)
            fetch.assert_not_called()
            ui(app, "analysis_sources").select("source:website").run()
            labels = ui(app, "analysis_datasets").options
            self.assertEqual(len(labels), 1)
            self.assertIn("03 Nov 2026, 12:00:00 UTC", labels[0])
            self.assertIn("Custom website · 1 record", labels[0])
            ui(app, "analysis_datasets").select("title:Mixed").run()
            ui(app, "analysis_sources").set_value(["source:facebook"]).run()
            self.assertEqual(ui(app, "analysis_datasets").value, [])
            self.assertIn("02 Nov 2026, 09:30:00 UTC", ui(app, "analysis_datasets").options[0])
            self.assertFalse(list(app.exception))


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
        self.stack.enter_context(patch("modules.auth.verify_access", return_value="editor@example.com"))
        self.stack.enter_context(patch("dashboard._render_ai_settings", return_value="openai"))
        self.stack.enter_context(patch("dashboard.get_ai_key", return_value="test-key"))
        self.catalog = self.stack.enter_context(patch("dashboard._fetch_analysis_datasets",
                                                     side_effect=lambda sources: dataset_options(self.rows, sources)))
        self.fetch = self.stack.enter_context(patch("dashboard._fetch_firestore_posts", side_effect=lambda status_filter, limit, source_urls, dataset:
            select_records([row for row in self.rows if status_filter == "all" or row["status"] == status_filter],
                           source_urls=source_urls, limit=len(self.rows) if limit is None else limit, dataset=dataset)))
        self.process = self.stack.enter_context(patch("dashboard._process_selected_firestore_posts",
            return_value={"processed": 1, "error": 0, "analyses": [], "errors": []}))
        self.app = AppTest.from_string("from dashboard import main\nmain()", default_timeout=30).run()
        self.assertFalse(list(self.app.exception))
        ui(self.app, "analysis_sources").select("source:website").run()

    def choose(self, key="title:Campaign A"):
        ui(self.app, "analysis_datasets").set_value([key] if isinstance(key, str) else key).run()
        self.assertFalse(list(self.app.exception))

    def ids(self):
        return {row["_id"] for row in self.app.session_state["firestore_rows"]}

    def test_dataset_opens_automatically_but_analysis_requires_a_click(self):
        self.assertEqual(ui(self.app, "analysis_datasets").value, [])
        self.fetch.assert_not_called()
        self.assertNotIn("load_firestore_records_btn", [button.key for button in self.app.button])
        self.assertNotIn("load_processed_records_btn", [button.key for button in self.app.button])
        self.choose()
        self.assertEqual(self.ids(), {"a1", "a2"})
        self.assertEqual(self.app.selectbox(key="analysis_record_status").value, "all")
        self.assertEqual(ui(self.app, "run_analysis_selected_btn").label, "Run ChatGPT on 2 included records")
        self.process.assert_not_called()
        self.app.run()
        self.fetch.assert_called_once()
        ui(self.app, "run_analysis_selected_btn").click().run()
        self.assertEqual({row["_id"] for row in self.process.call_args.args[0]}, {"a1", "a2"})

    def test_dataset_picker_shows_dates_and_sources(self):
        labels = ui(self.app, "analysis_datasets").options
        self.assertTrue(all("01 Nov 2026, 00:00:00 UTC" in label for label in labels))
        self.assertTrue(all("Custom website" in label for label in labels))

    def test_saved_exclusions_are_visible_and_not_sent_to_ai(self):
        self.rows[0]["analysis_excluded"] = True
        self.choose()
        self.assertEqual(self.ids(), {"a1", "a2"})
        self.assertEqual(ui(self.app, "run_analysis_selected_btn").label, "Run ChatGPT on 1 included record")
        ui(self.app, "run_analysis_selected_btn").click().run()
        self.assertEqual([row["_id"] for row in self.process.call_args.args[0]], ["a2"])

    def test_all_excluded_disables_analysis(self):
        for row in self.rows:
            row["analysis_excluded"] = True
        self.choose()
        self.assertTrue(ui(self.app, "run_analysis_selected_btn").disabled)
        self.process.assert_not_called()

    def test_mixed_dataset_and_injected_foreign_records_stay_out_of_ai(self):
        with patch("dashboard._render_analysis_sources", return_value=(["source:website"], True)):
            self.choose()
            self.app.session_state["firestore_rows"].extend([
                record("facebook", source_type="post", url="https://facebook.com/groups/old/posts/1"),
                record("foreign", "Other")])
            self.app.run()
            ui(self.app, "run_analysis_selected_btn").click().run()
        self.assertEqual({row["_id"] for row in self.process.call_args.args[0]}, {"a1", "a2"})

    def test_switching_datasets_opens_new_records_and_clears_old_results(self):
        self.choose()
        self.app.session_state["analysis_last_results"] = {"processed": 2, "analyses": []}
        self.app.session_state["content_recommendation_plan"] = {"old": True}
        old_editor = self.app.session_state["analysis_editor_key"]
        self.choose("title:Campaign B")
        self.assertEqual(self.ids(), {"b1"})
        self.assertNotEqual(self.app.session_state["analysis_editor_key"], old_editor)
        for key in ("analysis_last_results", "content_recommendation_plan"):
            self.assertNotIn(key, self.app.session_state)

    def test_status_changes_and_refresh_open_records_without_a_load_action(self):
        self.rows[0]["status"] = "processed"
        self.choose()
        self.app.selectbox(key="analysis_record_status").select("pending").run()
        self.assertEqual(ui(self.app, "run_analysis_selected_btn").label, "Run ChatGPT on 1 included record")
        ui(self.app, "refresh_analysis_datasets").click().run()
        self.assertEqual(self.ids(), {"a1", "a2"})
        self.assertFalse(self.app.exception)

    def test_processed_records_appear_automatically_and_exclusions_apply(self):
        self.rows[-1].update(status="processed", analysis={"sentiment": "Joy"})
        self.rows[0].update(status="processed", analysis_excluded=True)
        self.choose(["title:Campaign A", "untitled:"])
        self.assertEqual([r["_id"] for r in self.app.session_state["processed_rows"]], ["old"])

    def test_failed_open_has_retry_and_cannot_run_stale_records(self):
        self.fetch.side_effect = RuntimeError("offline")
        self.choose()
        self.assertTrue(self.app.error)
        self.assertEqual(self.ids(), set())
        self.assertNotIn("run_analysis_selected_btn", [button.key for button in self.app.button])
        self.fetch.side_effect = None
        self.fetch.return_value = [self.rows[0]]
        self.app.button(key="retry_analysis_dataset").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.ids(), {"a1"})

    def test_failed_catalog_refresh_clears_old_data(self):
        self.choose()
        self.catalog.side_effect = RuntimeError("offline")
        ui(self.app, "refresh_analysis_datasets").click().run()
        self.assertTrue(self.app.error)
        self.assertEqual(ui(self.app, "analysis_datasets").value, [])
        self.assertNotIn("firestore_rows", self.app.session_state)
        self.assertNotIn("run_analysis_selected_btn", [button.key for button in self.app.button])

    def test_multiple_datasets_have_no_hidden_fifty_or_two_hundred_record_cap(self):
        self.rows.extend(record(str(i), "Campaign B") for i in range(225))
        self.choose(["title:Campaign A", "title:Campaign B"])
        self.assertEqual(len(self.ids()), 228)
        ui(self.app, "run_analysis_selected_btn").click().run()
        self.assertEqual(len(self.process.call_args.args[0]), 228)
        self.assertIsNone(self.fetch.call_args.kwargs["limit"])

    def test_clearing_datasets_removes_all_runnable_records(self):
        self.choose(["title:Campaign A", "title:Campaign B"])
        self.choose([])
        self.assertNotIn("firestore_rows", self.app.session_state)
        self.assertEqual(self.app.session_state["processed_rows"], [])
        self.assertNotIn("run_analysis_selected_btn", [button.key for button in self.app.button])

    def test_reordering_the_same_datasets_does_not_refetch(self):
        self.choose(["title:Campaign A", "title:Campaign B"])
        key = self.app.session_state["analysis_editor_key"]
        self.choose(["title:Campaign B", "title:Campaign A"])
        self.fetch.assert_called_once()
        self.assertEqual(self.app.session_state["analysis_editor_key"], key)

    def test_refresh_removes_missing_datasets_and_opens_remaining_choices(self):
        self.choose(["title:Campaign A", "title:Campaign B"])
        self.rows[:] = [row for row in self.rows if row["custom_title"] != "Campaign B"]
        ui(self.app, "refresh_analysis_datasets").click().run()
        self.assertEqual(ui(self.app, "analysis_datasets").value, ["title:Campaign A"])
        self.assertEqual(self.ids(), {"a1", "a2"})

    def test_existing_single_dataset_choice_is_migrated(self):
        app = AppTest.from_string("from dashboard import _render_analysis_datasets\n_render_analysis_datasets(['source:website'], True)", default_timeout=30)
        app.session_state["analysis_dataset"] = "title:Campaign A"
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(ui(app, "analysis_datasets").value, ["title:Campaign A"])
        self.assertNotIn("analysis_dataset", app.session_state)

    def use_latest(self, rows=None, saved=True):
        recent = collection_snapshot(self.rows[:2] if rows is None else rows, "Custom website", 100)
        recent["saved"] = saved
        self.app.session_state["last_collected_dataset"] = recent
        ui(self.app, "analysis_selection_mode").set_value("Last scraped data").run()
        return recent

    def test_latest_is_exact_batch_and_opens_automatically(self):
        with patch("dashboard._fetch_last_scrape_posts", side_effect=lambda recent, **kwargs: recent["rows"] + [self.rows[1]]) as fetch:
            recent = self.use_latest(rows=[self.rows[0]])
            ui(self.app, "run_analysis_selected_btn").click().run()
            self.assertFalse(self.app.exception)
            self.assertEqual([r["_id"] for r in self.process.call_args.args[0]], [recent["rows"][0]["_id"]])
            self.assertEqual(fetch.call_args.kwargs["status_filter"], "all")
            self.fetch.assert_not_called()

    def test_unsaved_latest_saves_before_opening_and_failure_is_retryable(self):
        with patch("dashboard._write_results_to_firestore", side_effect=RuntimeError("offline")) as save, \
                patch("dashboard._fetch_last_scrape_posts", side_effect=lambda recent, **kwargs: recent["rows"]) as fetch:
            recent = self.use_latest(saved=False)
            self.assertTrue(self.app.error)
            fetch.assert_not_called()
            self.process.assert_not_called()
            save.side_effect = None
            self.app.button(key="retry_analysis_dataset").click().run()
            self.assertEqual(save.call_args.args[0], recent["rows"])
            fetch.assert_called_once()
            self.assertFalse(self.app.exception)
            self.assertEqual(ui(self.app, "run_analysis_selected_btn").label, "Run ChatGPT on 2 included records")

    def test_new_latest_replaces_old_records_and_results(self):
        with patch("dashboard._fetch_last_scrape_posts", side_effect=lambda recent, **kwargs: recent["rows"]):
            self.use_latest(rows=[self.rows[0]])
            old_key = self.app.session_state["analysis_editor_key"]
            self.app.session_state["analysis_last_results"] = {"analyses": []}
            recent = self.use_latest(rows=[self.rows[1]])
            self.assertEqual(self.ids(), {recent["rows"][0]["_id"]})
            self.assertNotEqual(self.app.session_state["analysis_editor_key"], old_key)
            self.assertNotIn("analysis_last_results", self.app.session_state)

    def test_no_recent_or_empty_recent_never_falls_back_to_saved_library(self):
        ui(self.app, "analysis_selection_mode").set_value("Last scraped data").run()
        self.use_latest(rows=[])
        self.assertNotIn("run_analysis_selected_btn", [button.key for button in self.app.button])
        self.fetch.assert_not_called()
        self.process.assert_not_called()

    def test_dialog_navigation_reads_live_state_instead_of_original_call_arguments(self):
        script = ("from dashboard import _analysis_steps_modal\n"
                  "_analysis_steps_modal('', {'ready': False}, 0, 'all', None, 'openai', 'ChatGPT')")
        app = AppTest.from_string(script, default_timeout=30)
        app.session_state["analysis_wizard_step"] = "Analyze"
        app.session_state["analysis_selection_memo"] = {
            "mode": "Manual selection", "sources": ["source:website"], "datasets": ["title:Campaign A"]}
        with patch("dashboard._render_analysis_run_step") as run:
            app.run()
        self.assertFalse(app.exception)
        self.assertTrue(run.call_args.args[0]["ready"])
        self.assertEqual(run.call_args.args[0]["datasets"], ["title:Campaign A"])


if __name__ == "__main__":
    unittest.main()
