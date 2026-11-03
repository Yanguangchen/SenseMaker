"""Deletion is explicit, limited to reviewed versions, and clears local working copies."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.database import delete_records, get_post_id, load_record_catalog
from modules.record_management_ui import filter_records
from modules.record_state import purge_record_caches


def record(ident, **extra):
    return {"_id": ident, "_version": "2026-11-03T00:00:00+00:00", "custom_title": "Campaign A",
            "title": "Page " + ident, "url": f"https://example.com/{ident}", "source_type": "website_page",
            "raw_text": "Feedback " + ident, "status": "pending", "scraped_at": "2026-11-03T00:00:00Z", **extra}


class RecordDatabaseTests(unittest.TestCase):
    def test_catalog_reads_only_metadata_and_uses_authoritative_id_and_version(self):
        db = MagicMock()
        stamp = datetime(2026, 11, 3, tzinfo=timezone.utc)
        db.collection.return_value.select.return_value.stream.return_value = [
            SimpleNamespace(id="actual", update_time=stamp, to_dict=lambda: record("wrong", _version="wrong"))]
        rows = load_record_catalog(db)
        self.assertEqual(rows[0]["_id"], "actual")
        self.assertEqual(rows[0]["_version"], stamp.isoformat())
        fields = db.collection.return_value.select.call_args.args[0]
        self.assertNotIn("raw_text", fields)
        self.assertNotIn("analysis", fields)

    def test_transaction_rechecks_versions_and_deletes_only_unchanged_selected_records(self):
        db = MagicMock()
        ids = ["pending", "processed", "error", "removed", "changed", "missing"]
        refs = {ident: SimpleNamespace(id=ident) for ident in ids}
        db.collection.return_value.document.side_effect = refs.__getitem__
        stamp = datetime(2026, 11, 3, tzinfo=timezone.utc)
        transaction = db.transaction.return_value

        def snapshots(*args, **kwargs):
            for ident in reversed(ids):
                transaction.delete.assert_not_called()  # Firestore requires all reads before writes.
                version = stamp.replace(day=4) if ident == "changed" else stamp
                yield SimpleNamespace(id=ident, exists=ident != "missing", update_time=version)

        db.get_all.side_effect = snapshots
        with patch("modules.database.firestore.transactional", side_effect=lambda function: function):
            result = delete_records(db, {ident: stamp.isoformat() for ident in ids})
        self.assertEqual(result, {"deleted": ids[:4], "skipped": ["changed"], "missing": ["missing"]})
        self.assertEqual([call.args[0].id for call in transaction.delete.call_args_list], ids[:4])
        self.assertEqual(db.get_all.call_args.kwargs["transaction"], transaction)

    def test_invalid_paths_versions_and_large_selection_never_reach_firestore(self):
        db = MagicMock()
        for selection in ({"": "version"}, {"nested/id": "version"}, {"..": "version"}, {None: "version"},
                          {"valid": ""}, {"valid": None}, {str(i): "version" for i in range(201)}):
            with self.subTest(selection=list(selection)[:2]), self.assertRaises(ValueError):
                delete_records(db, selection)
        self.assertEqual(delete_records(db, {}), {"deleted": [], "skipped": [], "missing": []})
        db.collection.assert_not_called()

    def test_read_failure_performs_no_deletes(self):
        db = MagicMock()
        db.get_all.side_effect = RuntimeError("offline")
        with patch("modules.database.firestore.transactional", side_effect=lambda function: function):
            with self.assertRaises(RuntimeError):
                delete_records(db, {"a": "version"})
        db.transaction.return_value.delete.assert_not_called()


class RecordFilterAndCacheTests(unittest.TestCase):
    def test_filters_compose_and_untitled_remains_distinct_from_a_literal_title(self):
        rows = [record("a"), record("b", status="processed"), record("c", custom_title="Campaign B"),
                record("d", custom_title=""), record("e", custom_title="Untitled (older records)"),
                record("f", source_type="reddit_post")]
        self.assertEqual([r["_id"] for r in filter_records(rows, "title:Campaign A", "Custom website", "pending", "EXAMPLE Page")], ["a"])
        self.assertEqual([r["_id"] for r in filter_records(rows, "untitled:", None, None, "")], ["d"])
        self.assertEqual(filter_records(rows, None, None, None, "missing search"), [])

    def test_deleted_records_leave_analysis_latest_scrape_and_collector_working_copies(self):
        a, b = record(get_post_id("https://example.com/a"), url="https://example.com/a"), record("b")
        collected_a = {key: value for key, value in a.items() if key != "_id"}
        state = {"firestore_rows": [a, b], "processed_rows": [a, b], "record_manager_rows": [a, b],
                 "analysis_last_results": {"processed": 2, "analyses": [a, b]},
                 "last_collected_dataset": {"id": "latest", "rows": [a, b], "saved": True},
                 "last_scrape_results": [collected_a, b], "last_reddit_scrape_results": [collected_a, b],
                 "news_scan": {"rows": [collected_a, b]}, "x_collection": {"rows": [collected_a, b]},
                 "website_collection": {"rows": [collected_a, b]}, "content_recommendation_plan": {"old": True},
                 "analysis_dataset_catalog": {"rows": [a]}, "latest_report_pdf": b"old"}
        purge_record_caches(state, {"deleted": [a["_id"]], "skipped": [], "missing": []})
        for key in ("firestore_rows", "processed_rows", "record_manager_rows", "last_scrape_results", "last_reddit_scrape_results"):
            self.assertEqual(state[key], [b])
        for key in ("last_collected_dataset", "news_scan", "x_collection", "website_collection"):
            self.assertEqual(state[key]["rows"], [b])
        self.assertEqual(state["analysis_last_results"]["analyses"], [b])
        self.assertEqual(state["firestore_last_load_count"], 1)
        self.assertNotIn("content_recommendation_plan", state)
        self.assertNotIn("analysis_dataset_catalog", state)
        self.assertNotIn("latest_report_pdf", state)

    def test_concurrently_changed_records_clear_stale_views_but_keep_collected_copy(self):
        row = record("a")
        state = {"firestore_rows": [row], "website_collection": {"rows": [row]}}
        purge_record_caches(state, {"deleted": [], "missing": [], "skipped": ["a"]})
        self.assertEqual(state["firestore_rows"], [])
        self.assertEqual(state["website_collection"]["rows"], [row])


class RecordManagementUITests(unittest.TestCase):
    script = "from modules.record_management_ui import render_record_management\nrender_record_management()"

    def setUp(self):
        self.rows = [record("a"), record("b", status="processed"), record("c", custom_title="Campaign B", status="error")]
        self.db = patch("modules.record_management_ui.init_db", return_value="db").start()
        self.load = patch("modules.record_management_ui.load_record_catalog", side_effect=lambda db: self.rows.copy()).start()
        self.delete = patch("modules.record_management_ui.delete_records", return_value={
            "deleted": ["a"], "skipped": [], "missing": []}).start()
        self.addCleanup(patch.stopall)
        self.app = AppTest.from_string(self.script).run()

    def loaded(self):
        self.app.button(key="record_manager_refresh").click().run()
        self.assertFalse(self.app.exception)

    def review(self, ids=("a",)):
        self.app.multiselect(key="record_manager_selection").set_value(list(ids)).run()
        self.app.button(key="record_manager_review").click().run()
        self.assertFalse(self.app.exception)

    def test_no_automatic_database_read_selection_or_deletion(self):
        self.load.assert_not_called()
        self.loaded()
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.assertTrue(self.app.button(key="record_manager_review").disabled)
        self.delete.assert_not_called()

    def test_preview_then_confirmation_deletes_exact_versions_and_updates_counts(self):
        self.loaded()
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign A").run()
        self.app.session_state["firestore_rows"] = self.rows.copy()
        self.review()
        self.delete.assert_not_called()
        self.assertIn("cannot be undone", self.app.warning[0].value)
        self.assertEqual(self.app.dataframe[-1].value["ID"].tolist(), ["a"])
        self.app.button(key="record_manager_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.delete.assert_called_once_with("db", {"a": self.rows[0]["_version"]})
        self.assertEqual([r["_id"] for r in self.app.session_state["record_manager_rows"]], ["b", "c"])
        self.assertEqual([r["_id"] for r in self.app.session_state["firestore_rows"]], ["b", "c"])
        self.assertIn("Deleted 1", self.app.success[0].value)
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.assertEqual(self.app.selectbox(key="record_manager_dataset").value, "title:Campaign A")

    def test_deleting_last_matching_records_keeps_filter_instead_of_showing_other_datasets(self):
        self.loaded()
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign A").run()
        self.review(("a", "b"))
        self.delete.return_value = {"deleted": ["a", "b"], "skipped": [], "missing": []}
        self.app.button(key="record_manager_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.selectbox(key="record_manager_dataset").value, "title:Campaign A")
        self.assertTrue(any("No records match" in info.value for info in self.app.info))
        self.assertEqual([r["_id"] for r in self.app.session_state["record_manager_rows"]], ["c"])

    def test_cancel_and_changing_selection_invalidate_review(self):
        self.loaded()
        self.review()
        self.app.button(key="record_manager_cancel").click().run()
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.review()
        self.app.multiselect(key="record_manager_selection").set_value(["b"]).run()
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.delete.assert_not_called()

    def test_filters_page_and_refresh_clear_selection_and_pending_confirmation(self):
        self.rows += [record(f"extra-{i}") for i in range(60)]
        self.loaded()
        self.review(("extra-9",))
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign B").run()
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.app.button(key="record_manager_select_all").click().run()
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, ["c"])
        self.app.selectbox(key="record_manager_dataset").select(None).run()
        self.app.button(key="record_manager_select_all").click().run()
        self.assertEqual(len(self.app.multiselect(key="record_manager_selection").value), 50)
        self.app.button(key="record_manager_review").click().run()
        self.app.selectbox(key="record_manager_page").select(2).run()
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.app.button(key="record_manager_select_all").click().run()
        self.app.button(key="record_manager_review").click().run()
        self.app.button(key="record_manager_refresh").click().run()
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.delete.assert_not_called()

    def test_select_all_never_exceeds_200_or_includes_other_dataset(self):
        self.rows = [record(str(i)) for i in range(250)] + [record("foreign", custom_title="Campaign B")]
        self.loaded()
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign A").run()
        self.app.selectbox(key="record_manager_page_size").select(200).run()
        self.app.button(key="record_manager_select_all").click().run()
        selected = self.app.multiselect(key="record_manager_selection").value
        self.assertEqual(len(selected), 200)
        self.assertNotIn("foreign", selected)
        self.app.selectbox(key="record_manager_page").select(2).run()
        self.app.button(key="record_manager_select_all").click().run()
        self.assertEqual(len(self.app.multiselect(key="record_manager_selection").value), 50)

    def test_delete_failure_requires_reload_before_retrying(self):
        self.loaded()
        self.review()
        self.delete.side_effect = RuntimeError("offline")
        self.app.button(key="record_manager_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("record_manager_rows", self.app.session_state)
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.assertIn("could not be confirmed", self.app.error[0].value)

    def test_failed_refresh_cannot_leave_old_data_deletable(self):
        self.loaded()
        self.review()
        self.load.side_effect = RuntimeError("offline")
        self.app.button(key="record_manager_refresh").click().run()
        self.assertNotIn("record_manager_rows", self.app.session_state)
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.delete.assert_not_called()

    def test_missing_and_changed_records_reported_without_deletion(self):
        self.loaded()
        self.review(("a", "b"))
        self.delete.return_value = {"deleted": [], "skipped": ["a"], "missing": ["b"]}
        self.app.button(key="record_manager_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertIn("Kept 1", self.app.warning[0].value)
        self.assertTrue(any("already been deleted" in info.value for info in self.app.info))

    def test_search_and_clear_selection_do_not_delete(self):
        self.loaded()
        self.app.text_input(key="record_manager_search").set_value("example.com/b").run()
        self.app.button(key="record_manager_select_all").click().run()
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, ["b"])
        self.app.button(key="record_manager_review").click().run()
        self.app.button(key="record_manager_clear").click().run()
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
