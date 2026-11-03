"""Inline inclusion edits remain scoped, reversible and safe to retry."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from modules.database import save_dataset_inclusion


def record(ident, **extra):
    return {"_id": ident, "custom_title": "Research", "source_type": "website_page",
            "url": "https://example.com/" + ident, "raw_text": "Page " + ident, "status": "pending", **extra}


class InclusionPersistenceTests(unittest.TestCase):
    def test_updates_only_inclusion_metadata_and_reads_before_writes(self):
        db = MagicMock()
        rows = [record("a"), record("b")]
        refs = {row["_id"]: SimpleNamespace(id=row["_id"]) for row in rows}
        db.collection.return_value.document.side_effect = refs.__getitem__
        transaction = db.transaction.return_value
        def snapshots(*args, **kwargs):
            for row in reversed(rows):
                transaction.update.assert_not_called()
                yield SimpleNamespace(id=row["_id"], exists=True, to_dict=lambda row=row: row)
        db.get_all.side_effect = snapshots
        with patch("modules.database.firestore.transactional", side_effect=lambda fn: fn):
            save_dataset_inclusion(db, rows, {"a": False, "b": True})
        self.assertEqual(transaction.update.call_count, 2)
        for call, excluded in zip(transaction.update.call_args_list, (True, False)):
            self.assertEqual(set(call.args[1]), {"analysis_excluded", "dataset_edited_at"})
            self.assertEqual(call.args[1]["analysis_excluded"], excluded)

    def test_missing_moved_or_changed_source_records_abort_without_recreating_them(self):
        for current in (None, record("a", custom_title="Moved"), record("a", source_type="news_article")):
            with self.subTest(current=current):
                db = MagicMock()
                db.get_all.return_value = [SimpleNamespace(id="a", exists=current is not None, to_dict=lambda: current)]
                with patch("modules.database.firestore.transactional", side_effect=lambda fn: fn), self.assertRaises(ValueError):
                    save_dataset_inclusion(db, [record("a")], {"a": False})
                db.transaction.return_value.update.assert_not_called()

    def test_foreign_or_invalid_ids_are_rejected_before_access(self):
        db = MagicMock()
        for changes in ({"foreign": False}, {"a": "false"}, {"../a": False}):
            with self.assertRaises(ValueError):
                save_dataset_inclusion(db, [record("a")], changes)
        db.collection.assert_not_called()


class EditorUITests(unittest.TestCase):
    script = ("import streamlit as st\nfrom modules.analysis_dataset_ui import render_dataset_editor\n"
              "rows, saved = render_dataset_editor(st.session_state['rows'])\n"
              "st.session_state['included_ids'] = [row['_id'] for row in rows]\n"
              "st.button('Run analysis', disabled=not saved or not rows)\n")

    def setUp(self):
        # AppTest cannot submit data_editor cell edits. Supply the table returned
        # by the widget; browser verification covers the actual checkbox interaction.
        self.edits = {}
        self.editor_key = None
        def editor(frame, **kwargs):
            if self.editor_key != kwargs["key"]:
                self.edits = {}
                self.editor_key = kwargs["key"]
            st.session_state.setdefault(kwargs["key"], {})
            result = frame.copy()
            for index, values in self.edits.items():
                for column, value in values.items():
                    result.loc[index, column] = value
            return result
        widget = patch("modules.analysis_dataset_ui.st.data_editor", side_effect=editor)
        widget.start()
        self.addCleanup(widget.stop)

    def app(self):
        app = AppTest.from_string(self.script, default_timeout=30)
        app.session_state["rows"] = [record("a"), record("b", analysis_excluded=True)]
        return app.run()

    def edit(self, app, changes):
        self.edits = changes
        return app.run()

    def test_include_column_saves_exclusions_and_restores_them(self):
        with patch("modules.analysis_dataset_ui.init_db"), patch("modules.analysis_dataset_ui.save_dataset_inclusion") as save:
            app = self.app()
            self.assertEqual(app.session_state["included_ids"], ["a"])
            save.assert_not_called()
            self.edit(app, {0: {"Include": False}})
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["included_ids"], [])
            self.assertTrue(app.button[0].disabled)
            self.assertEqual(save.call_args.args[2], {"a": False})
            self.edit(app, {0: {"Include": True}, 1: {"Include": True}})
            self.assertEqual(app.session_state["included_ids"], ["a", "b"])
            self.assertEqual(save.call_args.args[2], {"a": True, "b": True})
            app.run()
            self.assertEqual(save.call_count, 2)

    def test_failed_save_keeps_edit_blocks_analysis_and_retries(self):
        with patch("modules.analysis_dataset_ui.init_db"), patch("modules.analysis_dataset_ui.save_dataset_inclusion", side_effect=RuntimeError("offline")) as save:
            app = self.app()
            self.edit(app, {1: {"Include": True}})
            self.assertTrue(app.error)
            self.assertTrue(app.button[-1].disabled)
            self.assertTrue(app.session_state["rows"][1]["analysis_excluded"])
            save.side_effect = None
            app.button(key="retry_dataset_edits").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.button[-1].disabled)
            self.assertEqual(app.session_state["included_ids"], ["a", "b"])

    def test_changed_record_membership_does_not_reapply_old_row_indices(self):
        with patch("modules.analysis_dataset_ui.init_db"), patch("modules.analysis_dataset_ui.save_dataset_inclusion") as save:
            app = self.app()
            self.edit(app, {0: {"Include": False}})
            previous_key = app.session_state["analysis_editor_key"]
            app.session_state["rows"] = [record("new")]
            app.run()
            self.assertNotEqual(app.session_state["analysis_editor_key"], previous_key)
            self.assertEqual(app.session_state["included_ids"], ["new"])
            self.assertEqual(save.call_count, 1)

    def test_reopening_a_hidden_editor_uses_saved_exclusions_not_the_old_baseline(self):
        with patch("modules.analysis_dataset_ui.init_db"), patch("modules.analysis_dataset_ui.save_dataset_inclusion") as save:
            app = self.app()
            self.edit(app, {0: {"Include": False}})
            # Streamlit drops the widget's own state while the Analyze step is shown.
            del app.session_state[app.session_state["analysis_editor_key"]]
            self.edits = {}
            app.run()
            self.assertEqual(app.session_state["included_ids"], [])
            self.assertEqual(save.call_count, 1)


if __name__ == "__main__":
    unittest.main()
