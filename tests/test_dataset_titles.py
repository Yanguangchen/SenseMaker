"""Automatic collector titles and metadata-only renaming in Clean & manage data."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.database import rename_dataset, get_post_id
from modules.record_state import rename_record_caches
from tests.test_record_management import ModalAppTest

STAMP = datetime(2026, 11, 7, tzinfo=timezone.utc)


def record(ident, title="Original"):
    return {"_id": ident, "_version": STAMP.isoformat(), "custom_title": title,
            "raw_text": "Public post text", "status": "processed", "source_type": "website_page",
            "url": f"https://example.com/{ident}", "saved_at": STAMP.isoformat(),
            "analysis": {"sentiment": "Neutral"}, "analysis_excluded": True}


def database(rows):
    db = MagicMock()
    db.collection.return_value.document.side_effect = lambda ident: SimpleNamespace(id=ident)
    def get_all(refs, **kwargs):
        return [SimpleNamespace(id=ref.id, exists=ref.id in rows,
                                update_time=datetime.fromisoformat(rows[ref.id]["_version"]) if ref.id in rows else STAMP,
                                to_dict=lambda ident=ref.id: deepcopy(rows.get(ident))) for ref in refs]
    db.get_all.side_effect = get_all
    db.transaction.return_value.update.side_effect = lambda ref, values: rows[ref.id].update(values)
    return db


class DatasetRenameTests(unittest.TestCase):
    def test_rename_changes_only_title_preserving_record_ids_dates_and_analyses(self):
        rows = {ident: record(ident) for ident in ("a", "b")}
        before = deepcopy(rows)
        db = database(rows)
        with patch("modules.database.firestore.transactional", side_effect=lambda f: f):
            result = rename_dataset(db, "title:Original", {ident: STAMP.isoformat() for ident in rows}, "  Reviewed  ")
        self.assertEqual(result, {"renamed": ["a", "b"], "skipped": [], "missing": [], "unconfirmed": []})
        self.assertEqual(rows, {ident: {**row, "custom_title": "Reviewed"} for ident, row in before.items()})
        self.assertTrue(all(call.args[1] == {"custom_title": "Reviewed"} for call in db.transaction.return_value.update.call_args_list))

    def test_changed_moved_and_missing_records_are_not_renamed(self):
        rows = {ident: record(ident) for ident in ("a", "changed", "moved")}
        rows["changed"]["_version"] = STAMP.replace(day=8).isoformat()
        rows["moved"]["custom_title"] = "Another dataset"
        with patch("modules.database.firestore.transactional", side_effect=lambda f: f):
            result = rename_dataset(database(rows), "title:Original", {ident: STAMP.isoformat() for ident in (*rows, "missing")}, "New")
        self.assertEqual(result["renamed"], ["a"])
        self.assertEqual(result["skipped"], ["changed", "moved"])
        self.assertEqual(result["missing"], ["missing"])

    def test_validation_precedes_writes_and_partial_failures_report_progress(self):
        rows = {str(i): record(str(i)) for i in range(201)}
        db = database(rows)
        for title in ("", " ", "a" * 161):
            with self.assertRaises(ValueError):
                rename_dataset(db, "title:Original", {"0": STAMP.isoformat()}, title)
        with self.assertRaises(ValueError):
            rename_dataset(db, "title:Original", {"invalid/path": STAMP.isoformat()}, "New")
        db.get_all.assert_not_called()
        get_all = db.get_all.side_effect
        db.get_all.side_effect = lambda refs, **kwargs: get_all(refs, **kwargs) if refs[0].id == "0" else (_ for _ in ()).throw(RuntimeError("offline"))
        with patch("modules.database.firestore.transactional", side_effect=lambda f: f):
            result = rename_dataset(db, "title:Original", {ident: STAMP.isoformat() for ident in rows}, "New")
        self.assertEqual(len(result["renamed"]), 200)
        self.assertEqual(result["unconfirmed"], ["200"])

    def test_collection_caches_keep_the_new_title_on_later_save_or_retry(self):
        row = record(get_post_id("https://example.com/a"))
        row["url"] = "https://example.com/a"
        state = {"last_collected_dataset": {"rows": [deepcopy(row)], "title": "Original", "saved": True},
                 "website_collection": {"rows": [deepcopy(row)], "cloud_save": {"title": "Original", "state": "saved"}},
                 "collection_titles": {"save": {"ids": [row["_id"]], "title": "Original"}},
                 "analysis_dataset_catalog": {"old": True}, "record_manager_rows": [deepcopy(row)]}
        rename_record_caches(state, [row["_id"]], "New")
        self.assertEqual(state["last_collected_dataset"]["title"], "New")
        self.assertEqual(state["website_collection"]["cloud_save"]["title"], "New")
        self.assertEqual(state["collection_titles"]["save"]["title"], "New")
        self.assertEqual(state["last_collected_dataset"]["rows"][0]["analysis"], row["analysis"])
        self.assertNotIn("analysis_dataset_catalog", state)


class DatasetTitleUITests(unittest.TestCase):
    def test_facebook_and_reddit_save_without_title_inputs_or_legacy_custom_names(self):
        row = record("a")
        for source in ("Facebook", "Reddit"):
            with self.subTest(source=source), patch("dashboard._write_results_to_firestore", return_value={"inserted": 1, "skipped": 0}) as save:
                app = AppTest.from_string(f"""import streamlit as st
from dashboard import _render_cloud_save
st.session_state['custom_title_value'] = 'Old custom input'
st.session_state['reddit_custom_title_value'] = 'Old custom input'
_render_cloud_save([{row!r}], 'save_test', source={source!r})
""").run()
                self.assertFalse(app.text_input)
                app.button(key="save_test").click().run()
                title = save.call_args.kwargs["custom_title"]
                self.assertTrue(title.startswith(source + " · "))
                app.button(key="save_test").click().run()
                self.assertEqual(save.call_args.kwargs["custom_title"], title)

    def test_manage_data_renames_all_dataset_records_even_with_source_status_filters(self):
        rows = {ident: record(ident) for ident in ("a", "b")}
        rows["b"]["status"] = "pending"
        rows["c"] = record("c", "Another")
        before = deepcopy(rows)
        db = database(rows)
        with patch("modules.database.firestore.transactional", side_effect=lambda f: f), \
                patch("modules.record_management_ui.init_db", return_value=db), \
                patch("modules.record_management_ui.load_record_catalog", side_effect=lambda db: deepcopy(list(rows.values()))):
            app = ModalAppTest(AppTest.from_string(
                "from modules.record_management_ui import render_record_management\nrender_record_management()")).run()
            self.assertFalse(any(field.label == "Dataset title" for field in app.text_input))
            app.button(key="record_manager_browse_all").click().run()
            app.selectbox(key="record_manager_dataset").set_value("title:Original").run()
            app.selectbox(key="record_manager_status").set_value("pending").run()
            app.text_input(key="record_manager_title_title:Original").set_value("Reviewed")
            next(button for button in app.button if button.label == "Save dataset title").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="record_manager_dataset").value, "title:Reviewed")
            self.assertTrue(any('Dataset renamed' in notice.value for notice in app.success))
            self.assertEqual(rows["a"], {**before["a"], "custom_title": "Reviewed"})
            self.assertEqual(rows["b"], {**before["b"], "custom_title": "Reviewed"})
            self.assertEqual(rows["c"], before["c"])
