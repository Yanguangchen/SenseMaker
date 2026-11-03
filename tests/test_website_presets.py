"""Website URL presets persist privately and never trigger collection implicitly."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google.api_core.exceptions import AlreadyExists, NotFound
from streamlit.testing.v1 import AppTest

from modules import website_presets as store


class MemoryDB:
    def __init__(self):
        self.rows = {}
        self.fail_reads = False
        self.fail_writes = False

    def collection(self, name):
        return MemoryRef(self, name)


class MemoryRef:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def document(self, name):
        return MemoryRef(self.db, self.path + "/" + name)

    collection = document

    def stream(self, **kwargs):
        if self.db.fail_reads:
            raise RuntimeError("offline")
        return [SimpleNamespace(id=path.rsplit("/", 1)[1], to_dict=lambda row=row: deepcopy(row))
                for path, row in self.db.rows.items() if path.rsplit("/", 1)[0] == self.path]

    def create(self, row, **kwargs):
        if self.db.fail_writes:
            raise RuntimeError("offline")
        if self.path in self.db.rows:
            raise AlreadyExists("exists")
        self.db.rows[self.path] = deepcopy(row)

    def update(self, row, **kwargs):
        if self.db.fail_writes:
            raise RuntimeError("offline")
        if self.path not in self.db.rows:
            raise NotFound("missing")
        self.db.rows[self.path].update(deepcopy(row))

    def delete(self, **kwargs):
        if self.db.fail_writes:
            raise RuntimeError("offline")
        self.db.rows.pop(self.path, None)


class WebsitePresetStoreTests(unittest.TestCase):
    def test_saved_presets_are_private_normalized_and_deduplicated(self):
        db = MemoryDB()
        first = store.save_preset(db, " OWNER@example.com ", " My  news ",
                                  "example.com/a#top\nhttps://example.com/a\nexample.org/b")
        second = store.save_preset(db, "other@example.com", "My news", "example.org/private")
        self.assertEqual(first["name"], "My news")
        self.assertEqual(first["urls"], ["https://example.com/a", "https://example.org/b"])
        self.assertEqual(store.list_presets(db, "owner@example.com"), [first])
        self.assertEqual(store.list_presets(db, "other@example.com"), [second])
        store.update_preset(db, "owner@example.com", first["id"], "example.com/changed")
        self.assertEqual(store.list_presets(db, "other@example.com"), [second])
        store.delete_preset(db, "owner@example.com", first["id"])
        self.assertEqual(store.list_presets(db, "owner@example.com"), [])
        self.assertEqual(store.list_presets(db, "other@example.com"), [second])

    def test_duplicate_names_cannot_overwrite_and_missing_updates_do_not_recreate(self):
        db = MemoryDB()
        row = store.save_preset(db, "owner@example.com", "My news", "example.com/original")
        with self.assertRaisesRegex(ValueError, "already exists"):
            store.save_preset(db, "owner@example.com", " MY NEWS ", "example.com/replacement")
        self.assertEqual(store.list_presets(db, "owner@example.com"), [row])
        store.delete_preset(db, "owner@example.com", row["id"])
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            store.update_preset(db, "owner@example.com", row["id"], "example.com/new")
        self.assertEqual(store.list_presets(db, "owner@example.com"), [])

    def test_invalid_names_links_ids_and_large_lists_never_write(self):
        db = MemoryDB()
        for name, urls in [("", "example.com"), ("x" * 121, "example.com"), ("Valid", ""),
                           ("Valid", "http://127.0.0.1"), ("Valid", "facebook.com/test"),
                           ("Valid", "\n".join(f"example.com/{i}" for i in range(101))),
                           ("Valid", "example.com/" + "x" * 200_000)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                store.save_preset(db, "owner@example.com", name, urls)
        for preset_id in ("", "../other", "nested/path"):
            with self.assertRaises(ValueError):
                store.delete_preset(db, "owner@example.com", preset_id)
        self.assertEqual(db.rows, {})

    def test_presets_keep_all_one_hundred_links(self):
        db = MemoryDB()
        urls = [f"https://example.com/{i}" for i in range(100)]
        store.save_preset(db, "owner@example.com", "Full list", "\n".join(urls))
        self.assertEqual(store.list_presets(db, "owner@example.com")[0]["urls"], urls)


class WebsitePresetUITests(unittest.TestCase):
    script = "from dashboard import _render_website_workspace\n_render_website_workspace('owner@example.com')"

    def setUp(self):
        self.db = MemoryDB()
        db_patch = patch("modules.website_presets_ui.database.init_db", return_value=self.db)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        collect_patch = patch("dashboard.collect_websites")
        self.collect = collect_patch.start()
        self.addCleanup(collect_patch.stop)
        # The steps open in a dialog, which re-checks the signed-in identity on every rerun.
        access_patch = patch("modules.auth.verify_access", return_value="owner@example.com")
        access_patch.start()
        self.addCleanup(access_patch.stop)

    def app(self):
        app = AppTest.from_string(self.script).run()
        app.button(key="open_website_steps").click().run()
        self.assertFalse(list(app.exception))
        return app

    def save(self, app, name="News", text="example.com/a\nexample.org/b"):
        app.text_area(key="website_links").set_value(text).run()
        app.text_input(key="website_preset_name").set_value(name).run()
        app.button(key="website_preset_save").click().run()
        self.assertFalse(list(app.exception))
        return store.list_presets(self.db, "owner@example.com")[0]["id"]

    def test_save_reload_load_and_update_survive_new_sessions_without_collection(self):
        app = self.app()
        ident = self.save(app)
        fresh = self.app()
        self.assertEqual(fresh.text_area(key="website_links").value, "")
        fresh.selectbox(key="website_preset_choice").set_value(ident).run()
        self.assertEqual(fresh.text_area(key="website_links").value, "")
        fresh.button(key="website_preset_load").click().run()
        self.assertFalse(list(fresh.exception))
        self.assertEqual(fresh.text_area(key="website_links").value,
                         "https://example.com/a\nhttps://example.org/b")
        fresh.text_area(key="website_links").set_value("example.com/edited").run()
        fresh.button(key="website_preset_update").click().run()
        reopened = self.app()
        reopened.selectbox(key="website_preset_choice").set_value(ident).run()
        reopened.button(key="website_preset_load").click().run()
        self.assertFalse(list(reopened.exception))
        self.assertEqual(reopened.text_area(key="website_links").value, "https://example.com/edited")
        self.collect.assert_not_called()

    def test_delete_requires_confirmation_and_keeps_the_input_and_other_data(self):
        app = self.app()
        self.save(app)
        self.db.rows["raw_posts/kept"] = {"raw_text": "Collected source"}
        app.button(key="website_preset_delete").click().run()
        self.assertEqual(len(store.list_presets(self.db, "owner@example.com")), 1)
        app.button(key="website_preset_cancel_delete").click().run()
        self.assertEqual(len(store.list_presets(self.db, "owner@example.com")), 1)
        app.button(key="website_preset_delete").click().run()
        app.button(key="website_preset_confirm_delete").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(store.list_presets(self.db, "owner@example.com"), [])
        self.assertEqual(app.text_area(key="website_links").value, "example.com/a\nexample.org/b")
        self.assertIn("raw_posts/kept", self.db.rows)
        self.collect.assert_not_called()

    def test_save_and_update_errors_preserve_typed_links_and_saved_data(self):
        app = self.app()
        self.save(app)
        original = store.list_presets(self.db, "owner@example.com")
        app.text_area(key="website_links").set_value("example.com/unsaved").run()
        app.text_input(key="website_preset_name").set_value("NEWS").run()
        app.button(key="website_preset_save").click().run()
        self.assertTrue(any("already exists" in item.value for item in app.error))
        self.db.fail_writes = True
        app.button(key="website_preset_update").click().run()
        self.assertTrue(any("could not be confirmed" in item.value for item in app.error))
        self.assertEqual(app.text_area(key="website_links").value, "example.com/unsaved")
        self.assertEqual(store.list_presets(self.db, "owner@example.com"), original)
        self.assertFalse(list(app.exception))

    def test_refresh_sees_other_sessions_and_storage_outage_does_not_block_collection(self):
        app = self.app()
        row = store.save_preset(self.db, "owner@example.com", "New elsewhere", "example.com/new")
        app.button(key="website_preset_refresh").click().run()
        app.selectbox(key="website_preset_choice").set_value(row["id"]).run()
        app.button(key="website_preset_load").click().run()
        self.assertEqual(app.text_area(key="website_links").value, "https://example.com/new")
        self.db.fail_reads = True
        app.button(key="website_preset_refresh").click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any("could not be loaded" in item.value for item in app.warning))
        self.assertEqual(app.text_area(key="website_links").value, "https://example.com/new")
        app.button(key="website_wizard_step_next").click().run()
        self.assertFalse(app.button(key="collect_website_pages").disabled)


if __name__ == "__main__":
    unittest.main()
