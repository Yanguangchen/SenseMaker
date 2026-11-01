"""Selected-record deletion, confirmation, and stale view handling."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.database import delete_processed_posts


class DeleteProcessedPostsTests(unittest.TestCase):
    def test_checks_status_in_transaction_and_deletes_only_selected_processed_documents(self):
        db = MagicMock()
        refs = {name: SimpleNamespace(id=name, path=f"sensemaker_posts/{name}")
                for name in ("processed", "changed", "gone")}
        db.collection.return_value.document.side_effect = refs.__getitem__
        transaction = db.transaction.return_value

        def snapshots(*args, **kwargs):
            # Firestore does not guarantee the same order as the requested references.
            for name in ("gone", "changed", "processed"):
                transaction.delete.assert_not_called()
                status = "processed" if name == "processed" else "pending"
                yield SimpleNamespace(id=name, exists=name != "gone",
                                      to_dict=lambda status=status: {"status": status})

        db.get_all.side_effect = snapshots
        with patch("modules.database._get_collection_name", return_value="sensemaker_posts"), \
             patch("modules.database.firestore.transactional", side_effect=lambda fn: fn):
            result = delete_processed_posts(db, ["processed", "changed", "gone", "processed"])
        self.assertEqual(result, {"deleted": ["processed"], "skipped": ["changed"], "missing": ["gone"]})
        db.collection.assert_called_once_with("sensemaker_posts")
        transaction.delete.assert_called_once_with(refs["processed"])
        self.assertEqual(db.get_all.call_args.kwargs["transaction"], transaction)
        self.assertEqual(len(db.get_all.call_args.args[0]), 3)

    def test_invalid_paths_and_oversized_selection_do_not_access_database(self):
        db = MagicMock()
        for ids in (["a/nested/document"], [""], [".."], [None], [str(i) for i in range(201)]):
            with self.assertRaises(ValueError):
                delete_processed_posts(db, ids)
        db.collection.assert_not_called()
        self.assertEqual(delete_processed_posts(db, []), {"deleted": [], "skipped": [], "missing": []})

    def test_fetch_uses_firestore_document_id_instead_of_stored_id(self):
        from dashboard import _fetch_firestore_posts
        db = MagicMock()
        doc = SimpleNamespace(id="real-id", to_dict=lambda: {"_id": "wrong-id", "status": "processed"})
        db.collection.return_value.stream.return_value = [doc]
        with patch("dashboard.init_db", return_value=db):
            self.assertEqual(_fetch_firestore_posts("all", 50)[0]["_id"], "real-id")


class DeleteProcessedUITests(unittest.TestCase):
    script = (
        "import streamlit as st\n"
        "from dashboard import _render_processed_deletion\n"
        "_render_processed_deletion(st.session_state.get('processed_rows', []))\n"
    )

    def app(self):
        app = AppTest.from_string(self.script)
        rows = [{"_id": name, "status": "processed", "custom_title": f"Record {name}"}
                for name in ("a", "b")]
        app.session_state["processed_rows"] = rows
        app.session_state["firestore_rows"] = rows + [{"_id": "c", "status": "pending"}]
        app.session_state["firestore_last_load_count"] = 3
        for key in ("gemini_last_results", "analysis_last_results"):
            app.session_state[key] = {"processed": 2, "error": 0, "analyses": rows}
        return app.run()

    def test_confirmation_required_cancel_and_changed_selection_never_delete(self):
        with patch("dashboard.delete_processed_posts") as delete:
            app = self.app()
            self.assertTrue(app.button(key="request_processed_delete").disabled)
            app.multiselect(key="processed_delete_selection").set_value(["a"]).run()
            app.button(key="request_processed_delete").click().run()
            delete.assert_not_called()
            self.assertIn("cannot be undone", app.warning[0].value)
            app.multiselect(key="processed_delete_selection").set_value(["b"]).run()
            self.assertFalse(any(button.key == "confirm_processed_delete" for button in app.button))
            app.button(key="request_processed_delete").click().run()
            app.button(key="cancel_processed_delete").click().run()
            self.assertFalse(list(app.exception))
            delete.assert_not_called()
            self.assertEqual(len(app.session_state["processed_rows"]), 2)

    def test_confirm_removes_only_selected_record_from_all_cached_views(self):
        with patch("dashboard.init_db", return_value="db"), \
             patch("dashboard.delete_processed_posts", return_value={
                 "deleted": ["a"], "skipped": [], "missing": []}) as delete:
            app = self.app()
            app.multiselect(key="processed_delete_selection").set_value(["a"]).run()
            app.button(key="request_processed_delete").click().run()
            app.button(key="confirm_processed_delete").click().run()
            self.assertFalse(list(app.exception))
            delete.assert_called_once_with("db", ["a"])
            self.assertEqual([r["_id"] for r in app.session_state["processed_rows"]], ["b"])
            self.assertEqual([r["_id"] for r in app.session_state["firestore_rows"]], ["b", "c"])
            self.assertEqual(app.session_state["firestore_last_load_count"], 2)
            for key in ("gemini_last_results", "analysis_last_results"):
                self.assertEqual(app.session_state[key]["processed"], 1)
                self.assertEqual([r["_id"] for r in app.session_state[key]["analyses"]], ["b"])
            self.assertEqual(app.multiselect(key="processed_delete_selection").value, [])
            self.assertIn("Deleted 1", app.success[0].value)

    def test_database_failure_keeps_records_and_shows_actionable_error(self):
        with patch("dashboard.init_db"), patch("dashboard.delete_processed_posts", side_effect=RuntimeError("offline")):
            app = self.app()
            app.multiselect(key="processed_delete_selection").set_value(["a"]).run()
            app.button(key="request_processed_delete").click().run()
            app.button(key="confirm_processed_delete").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.session_state["processed_rows"]), 2)
            self.assertEqual(len(app.session_state["firestore_rows"]), 3)
            self.assertIn("Reload processed records", app.error[0].value)


if __name__ == "__main__":
    unittest.main()
