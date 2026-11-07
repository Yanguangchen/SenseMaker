"""Deletion is explicit, limited to reviewed versions, and clears local working copies."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from modules.database import delete_dataset_records, delete_datasets_records, delete_records, get_post_id, load_record_catalog
from modules.analysis_library import dataset_options
from modules.record_management_ui import featured_datasets, filter_records
from modules.record_state import purge_record_caches


def record(ident, **extra):
    return {"_id": ident, "_version": "2026-11-03T00:00:00+00:00", "custom_title": "Campaign A",
            "title": "Page " + ident, "url": f"https://example.com/{ident}", "source_type": "website_page",
            "raw_text": "Feedback " + ident, "status": "pending", "scraped_at": "2026-11-03T00:00:00Z", **extra}


class RecordDatabaseTests(unittest.TestCase):
    def test_multiple_datasets_keep_batches_scoped_and_report_combined_progress(self):
        datasets = {"title:A": {f"a{i}": "version" for i in range(201)}, "untitled:": {"b": "version"}}
        progress = MagicMock()
        with patch("modules.database.delete_records", side_effect=lambda db, versions, **kwargs:
                   {"deleted": list(versions), "skipped": [], "missing": []}) as delete:
            result = delete_datasets_records("db", datasets, progress)
        self.assertEqual(len(result["deleted"]), 202)
        self.assertFalse(result["unconfirmed"])
        self.assertEqual([len(call.args[1]) for call in delete.call_args_list], [200, 1, 1])
        self.assertEqual([call.kwargs["dataset"] for call in delete.call_args_list], ["title:A", "title:A", "untitled:"])
        self.assertEqual([call.args for call in progress.call_args_list], [(200, 202), (201, 202), (202, 202)])

    def test_multiple_datasets_validate_every_selection_before_deleting_anything(self):
        with patch("modules.database.delete_dataset_records") as delete:
            for invalid in ({"title:A": {"a": "v"}, "title:": {"b": "v"}},
                            {"title:A": {"a": "v"}, "title:B": {"nested/id": "v"}},
                            {"title:A": {"a": "v"}, "title:B": {"a": "v"}}):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    delete_datasets_records("db", invalid)
            self.assertEqual(delete_datasets_records("db", {}),
                             {"deleted": [], "skipped": [], "missing": [], "unconfirmed": []})
            delete.assert_not_called()

    def test_multiple_datasets_stop_on_partial_failure_and_keep_confirmed_progress(self):
        datasets = {"title:A": {"a": "v"}, "title:B": {f"b{i}": "v" for i in range(201)},
                    "title:C": {"c": "v"}}
        def remove(db, versions, **kwargs):
            if "b200" in versions:
                raise RuntimeError("offline")
            return {"deleted": list(versions), "skipped": [], "missing": []}
        with patch("modules.database.delete_records", side_effect=remove) as delete:
            result = delete_datasets_records("db", datasets)
        self.assertEqual(len(result["deleted"]), 201)
        self.assertEqual(result["unconfirmed"], ["b200", "c"])
        self.assertEqual(delete.call_count, 3)

    def test_multiple_datasets_keep_changed_and_moved_records(self):
        stamp = datetime(2026, 11, 3, tzinfo=timezone.utc)
        db = MagicMock()
        db.collection.return_value.document.side_effect = lambda ident: SimpleNamespace(id=ident)
        def snapshots(refs, **kwargs):
            return [SimpleNamespace(id=ref.id, exists=True,
                    update_time=stamp.replace(day=4) if ref.id == "changed" else stamp,
                    to_dict=lambda: {"custom_title": "B"}) for ref in refs]
        db.get_all.side_effect = snapshots
        with patch("modules.database.firestore.transactional", side_effect=lambda function: function):
            result = delete_datasets_records(db, {"title:A": {"moved": stamp.isoformat(), "changed": stamp.isoformat()},
                                                 "title:B": {"b": stamp.isoformat()}})
        self.assertEqual(result["deleted"], ["b"])
        self.assertEqual(result["skipped"], ["moved", "changed"])
        self.assertEqual(db.transaction.return_value.delete.call_count, 1)

    def test_dataset_deletion_spans_batches_and_reports_partial_progress(self):
        versions = {str(i): "version" for i in range(450)}
        def remove(db, selected, **kwargs):
            if "400" in selected:
                raise RuntimeError("offline")
            return {"deleted": list(selected), "skipped": [], "missing": []}
        with patch("modules.database.delete_records", side_effect=remove) as delete:
            result = delete_dataset_records("db", "title:Campaign A", versions)
        self.assertEqual(len(result["deleted"]), 400)
        self.assertEqual(result["unconfirmed"], list(versions)[400:])
        self.assertEqual([len(call.args[1]) for call in delete.call_args_list], [200, 200, 50])
        self.assertTrue(all(call.kwargs["dataset"] == "title:Campaign A" for call in delete.call_args_list))

    def test_dataset_validation_happens_before_any_batch_can_delete(self):
        versions = {str(i): "version" for i in range(201)}
        versions["invalid/path"] = "version"
        with patch("modules.database.delete_records") as delete:
            for dataset, selected in ((None, {}), ("", {}), ("title:", {}), ("title:A", versions)):
                with self.assertRaises(ValueError):
                    delete_dataset_records("db", dataset, selected)
            delete.assert_not_called()

    def test_dataset_membership_is_checked_even_when_versions_match(self):
        stamp = datetime(2026, 11, 3, tzinfo=timezone.utc)
        db = MagicMock()
        db.collection.return_value.document.side_effect = lambda ident: SimpleNamespace(id=ident)
        db.get_all.return_value = [SimpleNamespace(id=ident, exists=True, update_time=stamp,
            to_dict=lambda title=title: {"custom_title": title})
            for ident, title in (("a", "Campaign A"), ("b", "Campaign B"), ("c", ""))]
        with patch("modules.database.firestore.transactional", side_effect=lambda function: function):
            result = delete_dataset_records(db, "title:Campaign A", {ident: stamp.isoformat() for ident in ("a", "b", "c")})
        self.assertEqual(result["deleted"], ["a"])
        self.assertEqual(result["skipped"], ["b", "c"])
        self.assertEqual(db.transaction.return_value.delete.call_count, 1)

    def test_empty_dataset_never_accesses_storage(self):
        db = MagicMock()
        result = delete_dataset_records(db, "untitled:", {})
        self.assertEqual(result, {"deleted": [], "skipped": [], "missing": [], "unconfirmed": []})
        db.collection.assert_not_called()

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
    def test_today_uses_local_save_date_and_includes_every_dataset(self):
        rows = [record(str(i), custom_title=f"Today {i}", saved_at="2026-11-03T17:30:00Z") for i in range(8)]
        rows += [record("old", custom_title="Yesterday", saved_at="2026-11-03T15:59:00Z"),
                 record("resaved", custom_title="Today 0", saved_at="2026-11-04T01:00:00Z")]
        now = datetime(2026, 11, 4, 10, tzinfo=ZoneInfo("Asia/Singapore"))
        is_today, groups = featured_datasets(dataset_options(rows, None), now)
        self.assertTrue(is_today)
        self.assertEqual(len(groups), 8)
        self.assertEqual(groups[0]["title"], "Today 0")
        self.assertEqual(groups[0]["count"], 2)
        self.assertNotIn("Yesterday", [group["title"] for group in groups])

    def test_no_today_falls_back_to_six_latest_and_keeps_undated_legacy_data(self):
        rows = [record(str(i), custom_title=f"Dataset {i}", saved_at=f"2026-11-02T{i:02}:00:00Z") for i in range(8)]
        rows += [record("legacy", custom_title="", scraped_at="invalid")]
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        is_today, groups = featured_datasets(dataset_options(rows, None), now)
        self.assertFalse(is_today)
        self.assertEqual([group["title"] for group in groups], [f"Dataset {i}" for i in range(7, 1, -1)])
        self.assertEqual(featured_datasets([], now), (False, []))
        self.assertEqual(featured_datasets(dataset_options(rows[-1:], None), now)[1][0]["title"],
                         "Untitled (older records)")

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
        self.delete_dataset = patch("modules.record_management_ui.delete_dataset_records").start()
        self.delete_datasets = patch("modules.record_management_ui.delete_datasets_records").start()
        self.addCleanup(patch.stopall)
        self.app = AppTest.from_string(self.script).run()

    def loaded(self):
        self.app.button(key="record_manager_refresh").click().run()
        if "record_manager_details" not in self.app.session_state or not self.app.session_state["record_manager_details"]:
            self.app.button(key="record_manager_browse_all").click().run()
        self.assertFalse(self.app.exception)

    def review(self, ids=("a",)):
        self.app.multiselect(key="record_manager_selection").set_value(list(ids)).run()
        self.app.button(key="record_manager_review").click().run()
        self.assertFalse(self.app.exception)

    def test_automatically_loads_cards_without_selecting_or_deleting_records(self):
        self.load.assert_called_once_with("db")
        self.assertEqual(len(self.app.dataframe), 0)
        self.assertEqual(len([button for button in self.app.button if "card_featured" in button.key]), 2)
        self.assertEqual(len([button for button in self.app.tabs[1].button if "card_search" in button.key]), 2)
        self.assertEqual(self.app.tabs[1].expander[0].label, "Show / hide filters")
        self.assertFalse(self.app.tabs[1].expander[0].proto.expanded)
        self.assertFalse(self.app.multiselect)
        self.app.run()
        self.load.assert_called_once_with("db")
        self.app.button(key="record_manager_browse_all").click().run()
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.assertTrue(self.app.button(key="record_manager_review").disabled)
        self.delete.assert_not_called()

    def test_card_opens_just_its_dataset_and_back_clears_deletion_review(self):
        self.app.button(key="record_manager_card_featured_title:Campaign A").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.selectbox(key="record_manager_dataset").value, "title:Campaign A")
        self.assertEqual(set(self.app.dataframe[0].value["ID"]), {"a", "b"})
        self.review()
        self.app.button(key="record_manager_back").click().run()
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.assertFalse(self.app.dataframe)
        self.app.button(key="record_manager_card_featured_title:Campaign B").click().run()
        self.assertEqual(self.app.dataframe[0].value["ID"].tolist(), ["c"])
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.delete.assert_not_called()

    def test_multiple_dataset_review_covers_all_records_and_only_deletes_after_confirmation(self):
        self.rows += [record(f"extra-{i}") for i in range(250)] + [record("keep", custom_title="Campaign C")]
        self.app.button(key="record_manager_refresh").click().run()
        self.app.toggle(key="record_manager_dataset_delete_mode").set_value(True).run()
        self.assertTrue(self.app.button(key="record_manager_delete_datasets_review").disabled)
        self.app.text_input(key="record_manager_dataset_search").set_value("example.com/a").run()
        self.app.button(key="record_manager_card_search_title:Campaign A").click().run()
        self.app.button(key="record_manager_card_featured_title:Campaign B").click().run()
        expected = {key: {row["_id"]: row["_version"] for row in self.rows if "title:" + row["custom_title"] == key}
                    for key in ("title:Campaign A", "title:Campaign B")}
        self.app.button(key="record_manager_delete_datasets_review").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["datasets_delete_pending"]["datasets"], expected)
        self.assertEqual(set(self.app.dataframe[0].value["Dataset"]), {"Campaign A", "Campaign B"})
        self.assertEqual(len(self.app.dataframe[1].value), 253)
        self.assertTrue(any("cannot be undone" in warning.value for warning in self.app.warning))
        self.delete_datasets.assert_not_called()
        self.app.session_state["firestore_rows"] = self.rows.copy()
        def remove(db, datasets, **kwargs):
            ids = [ident for versions in datasets.values() for ident in versions]
            self.rows = [row for row in self.rows if row["_id"] not in ids]
            return {"deleted": ids, "skipped": [], "missing": [], "unconfirmed": []}
        self.delete_datasets.side_effect = remove
        self.app.button(key="record_manager_delete_datasets_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.delete_datasets.call_args.args, ("db", expected))
        self.assertEqual([row["_id"] for row in self.app.session_state["record_manager_rows"]], ["keep"])
        self.assertEqual([row["_id"] for row in self.app.session_state["firestore_rows"]], ["keep"])
        self.assertEqual(self.app.session_state["record_manager_datasets_to_delete"], [])
        self.assertNotIn("datasets_delete_pending", self.app.session_state)
        self.assertTrue(any("Deleted 2 dataset(s) and 253 record(s)" in notice.value for notice in self.app.success))

    def test_dataset_selection_is_shared_across_tabs_and_search_pages(self):
        self.rows = [record(str(i), custom_title=f"Campaign {i:02}") for i in range(25)]
        self.app.button(key="record_manager_refresh").click().run()
        self.app.toggle(key="record_manager_dataset_delete_mode").set_value(True).run()
        self.app.button(key="record_manager_card_featured_title:Campaign 24").click().run()
        self.assertIn("Selected", self.app.button(key="record_manager_card_search_title:Campaign 24").label)
        self.app.button(key="record_manager_card_search_title:Campaign 24").click().run()
        self.assertEqual(self.app.session_state["record_manager_datasets_to_delete"], [])
        self.app.button(key="record_manager_card_search_title:Campaign 24").click().run()
        self.app.button(key="record_manager_dataset_page_3").click().run()
        self.app.button(key="record_manager_card_search_title:Campaign 00").click().run()
        self.app.text_input(key="record_manager_dataset_search").set_value("no matches").run()
        self.app.button(key="record_manager_delete_datasets_review").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(set(self.app.session_state["datasets_delete_pending"]["datasets"]),
                         {"title:Campaign 24", "title:Campaign 00"})
        self.delete_datasets.assert_not_called()

    def test_dataset_selection_changes_cancel_refresh_and_mode_exit_invalidate_review(self):
        self.app.toggle(key="record_manager_dataset_delete_mode").set_value(True).run()
        self.app.button(key="record_manager_card_featured_title:Campaign A").click().run()
        self.app.button(key="record_manager_delete_datasets_review").click().run()
        self.app.button(key="record_manager_card_featured_title:Campaign B").click().run()
        self.assertNotIn("datasets_delete_pending", self.app.session_state)
        for action in ("record_manager_datasets_cancel", "record_manager_refresh"):
            self.app.button(key="record_manager_delete_datasets_review").click().run()
            self.app.button(key=action).click().run()
            self.assertNotIn("datasets_delete_pending", self.app.session_state)
        self.app.button(key="record_manager_delete_datasets_review").click().run()
        self.app.session_state["record_manager_rows"] = [record("a", _version="new-version"), *self.rows[1:]]
        self.app.run()
        self.assertNotIn("datasets_delete_pending", self.app.session_state)
        self.app.button(key="record_manager_delete_datasets_review").click().run()
        self.app.button(key="record_manager_datasets_clear").click().run()
        self.assertEqual(self.app.session_state["record_manager_datasets_to_delete"], [])
        self.assertNotIn("datasets_delete_pending", self.app.session_state)
        self.app.button(key="record_manager_card_featured_title:Campaign A").click().run()
        self.app.button(key="record_manager_delete_datasets_review").click().run()
        self.app.toggle(key="record_manager_dataset_delete_mode").set_value(False).run()
        self.assertEqual(self.app.session_state["record_manager_datasets_to_delete"], [])
        self.assertNotIn("datasets_delete_pending", self.app.session_state)
        self.assertEqual(self.app.button(key="record_manager_card_featured_title:Campaign A").label, "Open dataset")
        self.delete_datasets.assert_not_called()

    def test_partial_multiple_dataset_deletion_requires_refresh_and_purges_stale_views(self):
        self.app.toggle(key="record_manager_dataset_delete_mode").set_value(True).run()
        for title in ("Campaign A", "Campaign B"):
            self.app.button(key=f"record_manager_card_featured_title:{title}").click().run()
        self.app.button(key="record_manager_delete_datasets_review").click().run()
        self.app.session_state["firestore_rows"] = self.rows.copy()
        self.delete_datasets.return_value = {"deleted": ["a"], "skipped": [], "missing": [], "unconfirmed": ["b", "c"]}
        self.app.button(key="record_manager_delete_datasets_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("record_manager_rows", self.app.session_state)
        self.assertNotIn("datasets_delete_pending", self.app.session_state)
        self.assertEqual(self.app.session_state["firestore_rows"], [])
        self.assertTrue(any("Deleted 1 record(s)" in warning.value for warning in self.app.warning))
        self.assertTrue(any("Could not confirm deletion of 2" in error.value for error in self.app.error))

    def test_new_records_in_selected_datasets_are_kept_and_removal_is_not_claimed(self):
        self.app.toggle(key="record_manager_dataset_delete_mode").set_value(True).run()
        for title in ("Campaign A", "Campaign B"):
            self.app.button(key=f"record_manager_card_featured_title:{title}").click().run()
        self.app.button(key="record_manager_delete_datasets_review").click().run()
        self.rows = [record("new")]
        self.delete_datasets.return_value = {"deleted": ["a", "b", "c"], "skipped": [], "missing": [], "unconfirmed": []}
        self.app.button(key="record_manager_delete_datasets_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("new", self.delete_datasets.call_args.args[1]["title:Campaign A"])
        self.assertEqual([row["_id"] for row in self.app.session_state["record_manager_rows"]], ["new"])
        self.assertFalse(self.app.success)
        self.assertTrue(any("1 of 2 datasets confirmed removed" in warning.value for warning in self.app.warning))

    def test_dataset_search_finds_older_data_and_opens_the_complete_dataset(self):
        self.rows += [record(f"new-{i}", custom_title=f"New {i}", saved_at="2026-11-04T01:00:00Z") for i in range(8)]
        self.app.button(key="record_manager_refresh").click().run()
        self.app.text_input(key="record_manager_dataset_search").set_value("EXAMPLE.com/a").run()
        buttons = [button.key for button in self.app.button if "card_search" in button.key]
        self.assertEqual(buttons, ["record_manager_card_search_title:Campaign A"])
        self.app.button(key=buttons[0]).click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(set(self.app.dataframe[0].value["ID"]), {"a", "b"})
        self.assertEqual(self.app.text_input(key="record_manager_search").value, "")
        self.app.button(key="record_manager_back").click().run()
        self.assertEqual(self.app.text_input(key="record_manager_dataset_search").value, "EXAMPLE.com/a")
        self.app.text_input(key="record_manager_dataset_search").set_value("not-found").run()
        self.assertTrue(any("No datasets match" in info.value for info in self.app.info))
        self.app.text_input(key="record_manager_dataset_search").set_value("   ").run()
        self.assertEqual(len([button for button in self.app.button if "card_search" in button.key]), 10)

    def test_search_paginates_datasets_and_resets_on_a_new_query(self):
        self.rows = [record(str(i), custom_title=f"Campaign {i:02}") for i in range(25)]
        self.app.button(key="record_manager_refresh").click().run()
        self.assertEqual(len([button for button in self.app.button if "card_search" in button.key]), 12)
        self.assertNotIn("record_manager_dataset_page", [field.key for field in self.app.selectbox])
        self.assertEqual([button.label for button in self.app.button if button.key.startswith("record_manager_dataset_page_")],
                         ["1", "2", "3"])
        self.assertTrue(self.app.button(key="record_manager_dataset_previous").disabled)
        self.assertEqual(self.app.button(key="record_manager_dataset_page_1").proto.type, "primary")
        keys = [button.key for button in self.app.tabs[1].button]
        self.assertLess(max(index for index, key in enumerate(keys) if "card_search" in key),
                        keys.index("record_manager_dataset_page_1"))
        self.app.button(key="record_manager_dataset_next").click().run()
        self.assertEqual(self.app.session_state["record_manager_dataset_page"], 2)
        self.app.button(key="record_manager_dataset_previous").click().run()
        self.assertEqual(self.app.session_state["record_manager_dataset_page"], 1)
        self.app.button(key="record_manager_dataset_page_3").click().run()
        self.assertEqual(len([button for button in self.app.button if "card_search" in button.key]), 1)
        self.assertTrue(self.app.button(key="record_manager_dataset_next").disabled)
        self.app.text_input(key="record_manager_dataset_search").set_value("campaign").run()
        self.assertEqual(self.app.session_state["record_manager_dataset_page"], 1)
        self.assertEqual(len([button for button in self.app.button if "card_search" in button.key]), 12)
        self.app.button(key="record_manager_dataset_page_3").click().run()
        self.assertEqual(len([button for button in self.app.button if "card_search" in button.key]), 1)
        self.app.text_input(key="record_manager_dataset_search").set_value("campaign 00").run()
        self.assertEqual([button.key for button in self.app.button if "card_search" in button.key],
                         ["record_manager_card_search_title:Campaign 00"])
        self.assertNotIn("record_manager_dataset_next", [button.key for button in self.app.button])
        self.app.text_input(key="record_manager_dataset_search").set_value("").run()
        self.assertEqual(self.app.session_state["record_manager_dataset_page"], 1)
        self.assertEqual(len([button for button in self.app.button if "card_search" in button.key]), 12)
        self.assertFalse(self.app.exception)

    def test_entire_dataset_covers_all_pages_and_statuses_despite_empty_search_results(self):
        self.rows += [record(f"extra-{i}") for i in range(250)]
        self.loaded()
        self.assertTrue(self.app.button(key="dataset_delete_review").disabled)
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign A").run()
        self.app.selectbox(key="record_manager_status").select("processed").run()
        self.app.text_input(key="record_manager_search").set_value("no matching record").run()
        self.app.button(key="dataset_delete_review").click().run()
        self.assertFalse(self.app.exception)
        self.delete_dataset.assert_not_called()
        expected = {row["_id"]: row["_version"] for row in self.rows if row["custom_title"] == "Campaign A"}
        self.assertEqual(self.app.session_state["dataset_delete_pending"]["versions"], expected)
        self.assertEqual(len(self.app.dataframe[0].value), 252)
        def remove(*args, **kwargs):
            self.rows = [row for row in self.rows if row["_id"] not in args[2]]
            return {"deleted": list(args[2]), "skipped": [], "missing": [], "unconfirmed": []}
        self.delete_dataset.side_effect = remove
        self.app.button(key="dataset_delete_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.delete_dataset.call_args.args, ("db", "title:Campaign A", expected))
        self.assertEqual([row["_id"] for row in self.app.session_state["record_manager_rows"]], ["c"])
        self.assertIsNone(self.app.selectbox(key="record_manager_dataset").value)
        self.assertTrue(any('Dataset "Campaign A" deleted' in notice.value for notice in self.app.success))

    def test_dataset_cancel_switch_and_refresh_never_delete(self):
        self.loaded()
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign A").run()
        self.app.button(key="dataset_delete_review").click().run()
        self.app.button(key="dataset_delete_cancel").click().run()
        self.assertNotIn("dataset_delete_pending", self.app.session_state)
        self.app.button(key="dataset_delete_review").click().run()
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign B").run()
        self.assertNotIn("dataset_delete_pending", self.app.session_state)
        self.app.button(key="dataset_delete_review").click().run()
        self.app.button(key="record_manager_refresh").click().run()
        self.assertNotIn("dataset_delete_pending", self.app.session_state)
        self.delete_dataset.assert_not_called()

    def test_partial_dataset_failure_reports_confirmed_deletions_and_requires_refresh(self):
        self.loaded()
        self.app.session_state["firestore_rows"] = self.rows.copy()
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign A").run()
        self.app.button(key="dataset_delete_review").click().run()
        self.delete_dataset.return_value = {"deleted": ["a"], "skipped": [], "missing": [], "unconfirmed": ["b"]}
        self.app.button(key="dataset_delete_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("record_manager_rows", self.app.session_state)
        self.assertNotIn("dataset_delete_pending", self.app.session_state)
        self.assertTrue(any("Deleted 1 record" in notice.value for notice in self.app.warning))
        self.assertTrue(any("Could not confirm" in notice.value for notice in self.app.error))
        self.assertEqual([row["_id"] for row in self.app.session_state["firestore_rows"]], ["c"])

    def test_new_dataset_members_are_retained_and_dataset_is_not_reported_deleted(self):
        self.loaded()
        self.app.selectbox(key="record_manager_dataset").select("title:Campaign A").run()
        self.app.button(key="dataset_delete_review").click().run()
        self.rows = [record("new"), self.rows[-1]]
        self.delete_dataset.return_value = {"deleted": ["a", "b"], "skipped": [], "missing": [], "unconfirmed": []}
        self.app.button(key="dataset_delete_confirm").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(set(self.delete_dataset.call_args.args[2]), {"a", "b"})
        self.assertEqual(self.app.selectbox(key="record_manager_dataset").value, "title:Campaign A")
        self.assertFalse(self.app.success)
        self.assertTrue(any("incomplete" in notice.value for notice in self.app.warning))

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

    def test_row_delete_reviews_only_clicked_record_and_updates_dataset(self):
        self.rows[0]["title"] = self.rows[1]["title"] = "Same page title"
        self.app.button(key="record_manager_card_featured_title:Campaign A").click().run()
        self.app.session_state["firestore_rows"] = self.rows.copy()
        self.app.button(key="record_manager_select_all").click().run()
        self.app.button(key="record_manager_delete_b").click().run()
        self.assertFalse(self.app.exception)
        self.delete.assert_not_called()
        self.assertEqual(self.app.session_state["record_manager_row_pending"], {"b": self.rows[1]["_version"]})
        self.assertEqual(self.app.multiselect(key="record_manager_selection").value, [])
        self.assertIn("cannot be undone", self.app.warning[0].value)
        self.assertNotIn("record_manager_delete_c", [button.key for button in self.app.button])
        self.delete.return_value = {"deleted": ["b"], "skipped": [], "missing": []}
        self.app.button(key="record_manager_row_confirm_b").click().run()
        self.assertFalse(self.app.exception)
        self.delete.assert_called_once_with("db", {"b": self.rows[1]["_version"]})
        self.assertEqual([row["_id"] for row in self.app.session_state["record_manager_rows"]], ["a", "c"])
        self.assertEqual([row["_id"] for row in self.app.session_state["firestore_rows"]], ["a", "c"])
        self.assertEqual(self.app.dataframe[0].value["ID"].tolist(), ["a"])
        self.assertEqual(self.app.selectbox(key="record_manager_dataset").value, "title:Campaign A")
        self.assertNotIn("record_manager_row_pending", self.app.session_state)
        self.assertIn("Deleted 1", self.app.success[0].value)

    def test_row_cancel_and_switching_rows_never_delete(self):
        self.loaded()
        self.app.button(key="record_manager_delete_a").click().run()
        self.app.button(key="record_manager_delete_b").click().run()
        self.assertEqual(self.app.session_state["record_manager_row_pending"], {"b": self.rows[1]["_version"]})
        self.assertNotIn("record_manager_row_confirm_a", [button.key for button in self.app.button])
        self.app.button(key="record_manager_row_cancel_b").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("record_manager_row_pending", self.app.session_state)
        self.assertEqual(len(self.app.session_state["record_manager_rows"]), 3)
        self.delete.assert_not_called()

    def test_row_review_is_cleared_by_navigation_refresh_and_record_changes(self):
        self.rows += [record(f"extra-{i}") for i in range(60)]
        self.loaded()
        for change in ("dataset", "source", "status", "search", "page_size", "page", "refresh", "version", "back"):
            with self.subTest(change=change):
                self.app.button(key="record_manager_back").click().run()
                self.app.button(key="record_manager_card_featured_title:Campaign A").click().run()
                ident = self.app.dataframe[0].value["ID"].iloc[0]
                self.app.button(key=f"record_manager_delete_{ident}").click().run()
                if change in {"dataset", "source", "status", "page_size", "page"}:
                    key, value = {"dataset": ("dataset", "title:Campaign B"), "source": ("platform", "Custom website"),
                                  "status": ("status", "processed"), "page_size": ("page_size", 25), "page": ("page", 2)}[change]
                    self.app.selectbox(key=f"record_manager_{key}").select(value).run()
                elif change == "search":
                    self.app.text_input(key="record_manager_search").set_value("example.com/b").run()
                elif change == "version":
                    next(row for row in self.rows if row["_id"] == ident)["_version"] = "2026-11-05T00:00:00+00:00"
                    self.app.run()
                else:
                    self.app.button(key=f"record_manager_{change}").click().run()
                self.assertFalse(self.app.exception)
                self.assertNotIn("record_manager_row_pending", self.app.session_state)
                self.assertFalse(any(button.key.startswith("record_manager_row_confirm_") for button in self.app.button))
        self.delete.assert_not_called()

    def test_row_bulk_and_dataset_reviews_are_mutually_exclusive(self):
        self.app.button(key="record_manager_card_featured_title:Campaign A").click().run()
        self.review()
        self.app.button(key="record_manager_delete_b").click().run()
        self.assertNotIn("record_manager_pending", self.app.session_state)
        self.app.button(key="record_manager_select_all").click().run()
        self.assertNotIn("record_manager_row_pending", self.app.session_state)
        self.app.button(key="record_manager_delete_a").click().run()
        self.app.multiselect(key="record_manager_selection").set_value(["b"]).run()
        self.assertNotIn("record_manager_row_pending", self.app.session_state)
        self.app.button(key="dataset_delete_review").click().run()
        self.app.button(key="record_manager_delete_a").click().run()
        self.assertNotIn("dataset_delete_pending", self.app.session_state)
        self.app.button(key="dataset_delete_review").click().run()
        self.assertNotIn("record_manager_row_pending", self.app.session_state)
        self.assertFalse(self.app.exception)
        self.delete.assert_not_called()
        self.delete_dataset.assert_not_called()

    def test_row_delete_failure_requires_refresh_and_clears_review(self):
        self.loaded()
        self.app.button(key="record_manager_delete_a").click().run()
        self.delete.side_effect = RuntimeError("offline")
        self.app.button(key="record_manager_row_confirm_a").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("record_manager_row_pending", self.app.session_state)
        self.assertNotIn("record_manager_rows", self.app.session_state)
        self.assertIn("could not be confirmed", self.app.error[0].value)

    def test_deleting_last_row_keeps_dataset_scope(self):
        self.app.button(key="record_manager_card_featured_title:Campaign B").click().run()
        self.app.button(key="record_manager_delete_c").click().run()
        self.delete.return_value = {"deleted": ["c"], "skipped": [], "missing": []}
        self.app.button(key="record_manager_row_confirm_c").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.selectbox(key="record_manager_dataset").value, "title:Campaign B")
        self.assertTrue(any("No records match" in info.value for info in self.app.info))
        self.assertEqual([row["_id"] for row in self.app.session_state["record_manager_rows"]], ["a", "b"])

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
