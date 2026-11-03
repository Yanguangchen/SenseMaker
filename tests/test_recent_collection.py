"""The latest collection is exact membership, not a title or a timestamp range."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules.database import get_post_id
from modules.recent_collection import collection_snapshot


def page(key, **extra):
    return {"url": f"https://example.com/{key}", "raw_text": f"Text {key}",
            "source_type": "website_page", **extra}


class RecentCollectionTests(unittest.TestCase):
    def test_snapshot_deduplicates_and_excludes_errors_removed_and_empty_text(self):
        rows = [page("a"), page("a"), page("b", source_type="batch_error"),
                page("c", raw_text=""), page("d", content_removed=True), page("e", url="")]
        snapshot = collection_snapshot(rows, "Custom website", 100)
        self.assertEqual([row["_id"] for row in snapshot["rows"]], [get_post_id(rows[0]["url"])])
        self.assertFalse(snapshot["saved"])

    def test_snapshot_detaches_mutable_content_and_always_has_new_identity(self):
        rows = [page("a", comments=["First comment"])]
        first = collection_snapshot(rows, "Facebook", 100)
        second = collection_snapshot(rows, "Facebook", 100)
        rows[0]["comments"].append("Later comment")
        self.assertEqual(first["rows"][0]["comments"], ["First comment"])
        self.assertNotEqual(first["id"], second["id"])

    def test_empty_run_replaces_old_data_but_delayed_background_completion_does_not(self):
        from dashboard import _remember_collection
        state = {}
        with patch("dashboard.st.session_state", state):
            _remember_collection([page("a")], "Custom website", 200)
            _remember_collection([page("old")], "Facebook", 100)
            self.assertEqual(state["last_collected_dataset"]["source"], "Custom website")
            _remember_collection([], "X", 300)
            self.assertEqual(state["last_collected_dataset"]["source"], "X")
            self.assertEqual(state["last_collected_dataset"]["rows"], [])

    def test_loader_requests_exact_ids_and_filters_status_before_limit(self):
        from dashboard import _fetch_last_scrape_posts
        recent = collection_snapshot([page("a"), page("b")], "Custom website", 100)
        a, b = recent["rows"]
        db = MagicMock()
        db.collection.return_value.document.side_effect = lambda ident: ident
        db.get_all.return_value = [
            SimpleNamespace(id=a["_id"], exists=True, to_dict=lambda: {**a, "status": "processed"}),
            SimpleNamespace(id=b["_id"], exists=True, to_dict=lambda: {**b, "_id": "wrong", "status": "pending"}),
            SimpleNamespace(id="foreign", exists=True, to_dict=lambda: page("foreign", status="pending")),
        ]
        with patch("dashboard.init_db", return_value=db):
            found = _fetch_last_scrape_posts(recent, "pending", 1)
        self.assertEqual([r["_id"] for r in found], [b["_id"]])
        self.assertEqual(db.get_all.call_args.args[0], [a["_id"], b["_id"]])
        db.collection.return_value.stream.assert_not_called()

    def test_missing_records_are_ignored_and_empty_batch_does_not_read_library(self):
        from dashboard import _fetch_last_scrape_posts
        recent = collection_snapshot([page("a")], "Custom website", 100)
        db = MagicMock()
        db.get_all.return_value = [SimpleNamespace(id=recent["rows"][0]["_id"], exists=False)]
        with patch("dashboard.init_db", return_value=db):
            self.assertEqual(_fetch_last_scrape_posts(recent, "all", 50), [])
            db.reset_mock()
            self.assertEqual(_fetch_last_scrape_posts({"rows": []}, "all", 50), [])
            db.get_all.assert_not_called()

    def test_cloud_save_marks_only_matching_successful_collection_as_saved(self):
        from dashboard import _write_results_to_firestore
        recent = collection_snapshot([page("a", scraped_at="2026-11-03T00:00:00Z")], "Custom website", 100)
        state = {"last_collected_dataset": recent}
        with patch("dashboard.st.session_state", state), patch("dashboard.init_db"), \
                patch("dashboard.upsert_post") as save:
            _write_results_to_firestore([page("old")], "Older")
            self.assertFalse(recent["saved"])
            _write_results_to_firestore([page("a", scraped_at="2026-11-01T00:00:00Z")], "Same URL, older scrape")
            self.assertFalse(recent["saved"])
            save.side_effect = RuntimeError("offline")
            with self.assertRaises(RuntimeError):
                _write_results_to_firestore(recent["rows"], "Latest")
            self.assertFalse(recent["saved"])
            save.side_effect = None
            _write_results_to_firestore(recent["rows"], "My selected title")
            self.assertTrue(recent["saved"])
            self.assertEqual(recent["title"], "My selected title")


if __name__ == "__main__":
    unittest.main()
