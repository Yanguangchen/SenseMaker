"""Collection results persist independently of timeline summaries and UI reruns."""

from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from firebase_admin import firestore
from streamlit.testing.v1 import AppTest

from modules.database import get_post_id, load_record_catalog
from modules.google_news import assess_article


def record(kind, suffix="a", **extra):
    return {"url": f"https://example.com/{suffix}", "source_type": kind, "raw_text": "Thailand update",
            "title": "Thailand update", "scraped_at": "2026-11-03T00:00:00Z",
            "created_at": "2026-11-02T00:00:00Z", **extra}


class MemoryFirestore:
    def __init__(self):
        self.rows = {}
        self.writes = 0
        self.fail_write = None

    def collection(self, name):
        store = self

        class Query:
            def __init__(self, status=None):
                self.status = status

            def document(self, ident):
                key = (name, ident)

                def get():
                    return SimpleNamespace(exists=key in store.rows, to_dict=lambda: deepcopy(store.rows.get(key)))

                def save(payload, merge=False):
                    store.writes += 1
                    if store.writes == store.fail_write:
                        raise RuntimeError("offline")
                    row = dict(store.rows.get(key, {})) if merge else {}
                    for field, value in payload.items():
                        if value is firestore.DELETE_FIELD:
                            row.pop(field, None)
                        else:
                            row[field] = deepcopy(value)
                    store.rows[key] = row

                return SimpleNamespace(get=get, set=save)

            def select(self, fields):
                return self

            def where(self, field, operation, value):
                return Query(value)

            def stream(self, **kwargs):
                return [SimpleNamespace(id=ident, update_time=datetime(2026, 11, 3, tzinfo=timezone.utc),
                                        to_dict=lambda row=row: deepcopy(row))
                        for (collection, ident), row in store.rows.items() if collection == name
                        and (self.status is None or row.get("status") == self.status)]

        return Query()


class CollectionPersistenceTests(unittest.TestCase):
    def test_every_record_in_a_save_receives_the_same_dataset_timestamp(self):
        from dashboard import _write_results_to_firestore
        stamp = "2026-11-03T15:20:30+00:00"
        with patch("dashboard.init_db"), patch("dashboard.st.session_state", {}), \
             patch("dashboard._now_iso", return_value=stamp) as now, patch("dashboard.upsert_post") as save:
            _write_results_to_firestore([record("website_page", "a"), record("news_article", "b")], "Named dataset")
        self.assertEqual([call.args[1]["saved_at"] for call in save.call_args_list], [stamp, stamp])
        now.assert_called_once()

    def test_resaving_facebook_updates_dataset_date_without_replacing_analysis(self):
        from modules.database import upsert_post
        db = MagicMock()
        ref = db.collection.return_value.document.return_value
        ref.get.return_value.exists = True
        ref.get.return_value.to_dict.return_value = {"raw_text": "Original", "analysis": {"sentiment": "Joy"}}
        row = record("post", "fb", custom_title="New dataset", saved_at="2026-11-03T15:20:30+00:00")
        upsert_post(db, row)
        payload = ref.set.call_args.args[0]
        self.assertEqual(payload["custom_title"], "New dataset")
        self.assertEqual(payload["saved_at"], row["saved_at"])
        self.assertNotIn("analysis", payload)
        self.assertNotIn("raw_text", payload)

    def test_all_three_sources_round_trip_into_analysis_and_management_after_session_reset(self):
        from dashboard import _autosave_collection, _fetch_firestore_posts, _remember_collection
        db = MemoryFirestore()
        for kind, source, key in (("news_article", "Online news", "news_scan"),
                                  ("x_post", "X", "x_collection"),
                                  ("website_page", "Custom website", "website_collection")):
            with self.subTest(source=source):
                rows = [record(kind, kind)]
                collection = {"rows": rows}
                state = {key: collection, "analysis_dataset_catalog": {}, "record_manager_rows": [],
                         "record_manager_pending": {"old": "version"}}
                with patch("dashboard.st.session_state", state), patch("dashboard.init_db", return_value=db):
                    _remember_collection(rows, source)
                    _autosave_collection(collection, source, "Saved " + source)
                    self.assertEqual(collection["cloud_save"]["state"], "saved")
                    self.assertTrue(state["last_collected_dataset"]["saved"])
                    self.assertNotIn("record_manager_rows", state)
                    self.assertNotIn("record_manager_pending", state)
                    self.assertNotIn("analysis_dataset_catalog", state)
                # A fresh session has no collected rows; reads must come from storage.
                with patch("dashboard.st.session_state", {}), patch("dashboard.init_db", return_value=db):
                    saved = _fetch_firestore_posts("pending", 50, dataset="title:Saved " + source)
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["raw_text"], rows[0]["raw_text"])
                self.assertEqual(saved[0]["source_type"], kind)
                self.assertEqual(saved[0]["_id"], get_post_id(rows[0]["url"]))
        self.assertEqual({row["source_type"] for row in load_record_catalog(db)}, {"news_article", "x_post", "website_page"})

    def test_partial_save_retry_uses_same_title_and_ids_without_duplicate_documents(self):
        from dashboard import _autosave_collection
        db = MemoryFirestore()
        db.fail_write = 2
        rows = [record("website_page", "a"), record("website_page", "b")]
        collection = {"rows": rows}
        state = {"website_collection": collection}
        with patch("dashboard.st.session_state", state), patch("dashboard.init_db", return_value=db):
            _autosave_collection(collection, "Custom website", "Original title")
            self.assertEqual(collection["cloud_save"]["state"], "error")
            self.assertEqual(len(db.rows), 1)
            self.assertEqual(collection["rows"], rows)
            _autosave_collection(collection, "Custom website", "Changed input")
        self.assertEqual(collection["cloud_save"]["state"], "saved")
        self.assertEqual(len(db.rows), 2)
        self.assertEqual({row["custom_title"] for row in db.rows.values()}, {"Original title"})

    def test_warnings_and_duplicates_are_excluded_but_x_removals_are_persisted(self):
        from dashboard import _autosave_collection
        db = MemoryFirestore()
        rows = [record("x_post"), record("x_post"), record("x_reply", "reply", parent_text="Context"),
                record("x_post", "removed", raw_text="", content_removed=True),
                record("batch_warning", "warning"), record("batch_error", "error"), record("x_post", url="")]
        collection = {"rows": rows}
        with patch("dashboard.st.session_state", {}), patch("dashboard.init_db", return_value=db):
            _autosave_collection(collection, "X")
        self.assertEqual(len(db.rows), 3)
        self.assertTrue(collection["cloud_save"]["title"].startswith("X · "))
        self.assertEqual(collection["cloud_save"]["count"], 3)
        self.assertEqual({row["status"] for row in db.rows.values()}, {"pending", "removed"})
        self.assertTrue(any(row.get("parent_text") == "Context" for row in db.rows.values()))

    def test_empty_or_failed_collection_does_not_access_storage(self):
        from dashboard import _autosave_collection
        with patch("dashboard.st.session_state", {}), patch("dashboard.init_db") as db:
            for rows in ([], [record("batch_error")]):
                collection = {"rows": rows}
                _autosave_collection(collection, "X")
                self.assertEqual(collection["cloud_save"]["state"], "empty")
            db.assert_not_called()

    def test_recollecting_loaded_records_invalidates_stale_analysis_even_with_same_title(self):
        from dashboard import _autosave_collection
        old = {**record("website_page"), "_id": get_post_id("https://example.com/a"), "raw_text": "Old text"}
        state = {"firestore_rows": [old], "processed_rows": [old], "analysis_record_selection": [old["_id"]],
                 "analysis_last_results": {"analyses": [old]}, "content_recommendation_plan": {"old": True}}
        with patch("dashboard.st.session_state", state), patch("dashboard.init_db", return_value=MemoryFirestore()):
            _autosave_collection({"rows": [record("website_page")]}, "Custom website", "Same title")
        for key in ("firestore_rows", "processed_rows", "analysis_record_selection", "analysis_last_results", "content_recommendation_plan"):
            self.assertNotIn(key, state)

    def test_saving_from_last_scraped_data_also_clears_the_collectors_failed_save_state(self):
        from dashboard import _write_results_to_firestore
        rows = [record("website_page")]
        collection = {"rows": rows, "cloud_save": {"state": "error", "title": "Old title"}}
        with patch("dashboard.st.session_state", {"website_collection": collection}), \
                patch("dashboard.init_db", return_value=MemoryFirestore()):
            _write_results_to_firestore(rows, "Recovered")
        self.assertEqual(collection["cloud_save"], {"state": "saved", "title": "Recovered", "count": 1})


class CollectionAutosaveUITests(unittest.TestCase):
    cases = [("news", "news_scan", "news_custom_title_value", "collect_news_articles", "retry_news_save"),
             ("x", "x_collection", "x_custom_title_value", "collect_x_posts", "retry_x_save"),
             ("website", "website_collection", "website_custom_title", "collect_website_pages", "retry_website_save")]

    def app(self, stack, source, rows, save):
        collect = stack.enter_context(patch("dashboard.collect_" + {"news": "news", "x": "x", "website": "websites"}[source],
                                            return_value=rows))
        stack.enter_context(patch("dashboard._write_results_to_firestore", save))
        stack.enter_context(patch("dashboard.init_db"))
        stack.enter_context(patch("dashboard._load_news_history", return_value=[]))
        stack.enter_context(patch("dashboard.persist_news_scan"))
        stack.enter_context(patch("dashboard.x_configured", return_value=True))
        stack.enter_context(patch("dashboard.XClient", return_value=SimpleNamespace(reads=2)))
        app = AppTest.from_string(f"from dashboard import _render_{source}_workspace\n_render_{source}_workspace()").run()
        if source == "website":
            app.button(key="open_website_steps").click().run()
            app.text_area(key="website_links").set_value("https://example.com/a").run()
            app.button(key="website_wizard_step_next").click().run()
        return app, collect

    def rows(self, source):
        kind = {"news": "news_article", "x": "x_post", "website": "website_page"}[source]
        return [record(kind, news_category="boycott", news_categories=["boycott"],
                       news_assessment=assess_article("Thailand update")), record("batch_warning", "warning")]

    def set_title(self, app, source, title_key, title):
        if source == "x" and ("x_focus_open" not in app.session_state or not app.session_state["x_focus_open"]):
            # AppTest 1.50 retains closed dialog widgets after st.rerun().
            # Refresh without submitting their stale button triggers.
            app._run()
            app.button(key="open_x_collection").click().run()
        if source == "x" and "x_collect_back" in [button.key for button in app.button]:
            app.button(key="x_collect_back").click().run()
            app.button(key="x_options_back").click().run()
        app.text_input(key=title_key).set_value(title).run()
        if source == "x":
            app.button(key="x_name_next").click().run()
            app.button(key="x_options_next").click().run()

    def test_each_collector_saves_once_and_rerenders_do_not_recreate_or_resave_records(self):
        for source, state_key, title_key, collect_key, retry_key in self.cases:
            with self.subTest(source=source), ExitStack() as stack:
                save = MagicMock(return_value={"inserted": 1, "skipped": 0})
                rows = self.rows(source)
                app, collect = self.app(stack, source, rows, save)
                save.assert_not_called()
                self.set_title(app, source, title_key, "My dataset")
                app.button(key=collect_key).click().run()
                self.assertFalse(app.exception)
                save.assert_called_once_with(rows[:1], custom_title="My dataset")
                self.assertEqual(app.session_state[state_key]["cloud_save"]["state"], "saved")
                self.assertTrue(any("Saved 1 record" in notice.value for notice in app.success))
                self.set_title(app, source, title_key, "Next collection")
                app.run()
                self.assertEqual(save.call_count, 1)
                self.assertEqual(collect.call_count, 1)

    def test_failed_save_keeps_data_blocks_overwrite_and_retry_never_recollects(self):
        for source, state_key, title_key, collect_key, retry_key in self.cases:
            with self.subTest(source=source), ExitStack() as stack:
                save = MagicMock(side_effect=[RuntimeError("offline"), {"inserted": 1, "skipped": 0}])
                rows = self.rows(source)
                app, collect = self.app(stack, source, rows, save)
                self.set_title(app, source, title_key, "Original title")
                app.button(key=collect_key).click().run()
                self.assertFalse(app.exception)
                self.assertTrue(app.button(key=collect_key).disabled)
                self.assertEqual(app.session_state[state_key]["rows"], rows)
                self.assertTrue(any("Cloud saving failed" in item.value for item in app.error))
                self.set_title(app, source, title_key, "Next title")
                self.assertEqual(save.call_count, 1)
                app.button(key="retry_x_save_dialog" if source == "x" else retry_key).click().run()
                self.assertFalse(app.exception)
                if source == "x":
                    app._run()  # Discard stale triggers from the closed dialog.
                    app.button(key="open_x_collection").click().run()
                self.assertFalse(app.button(key=collect_key).disabled)
                self.assertEqual(app.session_state[state_key]["cloud_save"]["state"], "saved")
                self.assertEqual(collect.call_count, 1)
                self.assertEqual(save.call_args.kwargs["custom_title"], "Original title")


class WebsiteDatasetSaveUITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db = MemoryFirestore()
        self.stack.enter_context(patch("dashboard.init_db", return_value=self.db))
        self.rows = [record("website_page", "a"), record("website_page", "b"),
                     record("batch_warning", "warning")]
        self.collect = self.stack.enter_context(patch("dashboard.collect_websites", return_value=self.rows))
        self.app = AppTest.from_string(
            "from dashboard import _render_website_workspace\n_render_website_workspace()"
        ).run()
        self.app.button(key="open_website_steps").click().run()
        self.app.text_area(key="website_links").set_value("https://example.com/a\nhttps://example.com/b").run()
        self.app.button(key="website_wizard_step_next").click().run()

    def collect_pages(self, title=""):
        self.app.text_input(key="website_custom_title").set_value(title).run()
        self.app.button(key="collect_website_pages").click().run()
        self.assertFalse(self.app.exception)

    def test_save_changes_dataset_after_review_and_preserves_existing_analysis(self):
        from dashboard import _fetch_firestore_posts
        self.collect_pages("Original")
        for row in self.db.rows.values():
            row.update(status="processed", analysis={"sentiment": "Neutral"})
        self.app.session_state["analysis_dataset_catalog"] = {"stale": True}
        self.app.session_state["record_manager_rows"] = ["stale"]
        self.app.text_input(key="website_custom_title").set_value("  Reviewed pages  ").run()
        self.assertEqual(self.db.writes, 2)
        self.app.button(key="save_website_dataset").click().run()
        self.assertFalse(self.app.exception)
        self.collect.assert_called_once()
        self.assertEqual(len(self.db.rows), 2)
        self.assertEqual({row["custom_title"] for row in load_record_catalog(self.db)}, {"Reviewed pages"})
        with patch("dashboard.st.session_state", {}):
            saved = _fetch_firestore_posts("processed", 50, dataset="title:Reviewed pages")
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(row["analysis"] == {"sentiment": "Neutral"} for row in saved))
        self.assertEqual(self.app.session_state["last_collected_dataset"]["title"], "Reviewed pages")
        self.assertNotIn("analysis_dataset_catalog", self.app.session_state)
        self.assertNotIn("record_manager_rows", self.app.session_state)
        self.app.run()
        self.assertEqual(self.db.writes, 4)

    def test_blank_title_keeps_the_generated_dataset_title(self):
        self.collect_pages()
        title = self.app.session_state["website_collection"]["cloud_save"]["title"]
        self.app.button(key="save_website_dataset").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(title.startswith("Custom website · "))
        self.assertEqual({row["custom_title"] for row in self.db.rows.values()}, {title})
        self.assertEqual(len(self.db.rows), 2)

    def test_partial_manual_save_retries_the_chosen_title_without_recollecting(self):
        self.collect_pages("Original")
        self.db.fail_write = self.db.writes + 2
        self.app.text_input(key="website_custom_title").set_value("Reviewed pages").run()
        self.app.button(key="save_website_dataset").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(self.app.button(key="collect_website_pages").disabled)
        self.assertTrue(self.app.button(key="save_website_dataset").disabled)
        self.assertEqual(self.app.session_state["website_collection"]["rows"], self.rows)
        self.assertTrue(any("Cloud saving failed" in notice.value for notice in self.app.error))
        self.app.text_input(key="website_custom_title").set_value("Next collection").run()
        self.app.button(key="retry_website_save").click().run()
        self.assertFalse(self.app.exception)
        self.collect.assert_called_once()
        self.assertEqual({row["custom_title"] for row in self.db.rows.values()}, {"Reviewed pages"})
        self.assertEqual(self.app.session_state["last_collected_dataset"]["title"], "Reviewed pages")
        self.assertFalse(self.app.button(key="save_website_dataset").disabled)
        self.assertFalse(self.app.button(key="collect_website_pages").disabled)

    def test_unsaved_session_results_can_be_saved_and_empty_results_cannot(self):
        self.app.session_state["website_collection"] = {
            "urls": [row["url"] for row in self.rows[:2]], "rows": self.rows,
        }
        self.app.text_input(key="website_custom_title").set_value("Recovered pages").run()
        self.app.button(key="save_website_dataset").click().run()
        self.assertFalse(self.app.exception)
        self.collect.assert_not_called()
        self.assertEqual({row["custom_title"] for row in self.db.rows.values()}, {"Recovered pages"})
        self.collect.return_value = self.rows[2:]
        self.app.button(key="collect_website_pages").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("save_website_dataset", [button.key for button in self.app.button])
        self.assertEqual(self.db.writes, 2)


if __name__ == "__main__":
    unittest.main()
