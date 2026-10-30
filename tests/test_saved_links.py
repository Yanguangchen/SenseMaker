"""Persistent libraries, account isolation, bulk operations and large scrape jobs."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules.saved_links import load_saved_links, parse_links, remove_saved_links, save_links


class MemoryCollection:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def document(self, name):
        return MemoryCollection(self.db, self.path + "/" + name)

    def collection(self, name):
        return self.document(name)

    def stream(self, **kwargs):
        return [
            SimpleNamespace(to_dict=lambda value=value: value.copy())
            for key, value in self.db.records.items()
            if key.rsplit("/", 1)[0] == self.path
        ]


class MemoryDB:
    def __init__(self):
        self.records = {}
        self.commits = []
        self.fail_commit = None

    def collection(self, name):
        return MemoryCollection(self, name)

    def batch(self):
        db = self

        class Batch:
            def __init__(self):
                self.writes = []

            def set(self, ref, payload):
                self.writes.append((ref.path, payload))

            def delete(self, ref):
                self.writes.append((ref.path, None))

            def commit(self, **kwargs):
                if len(db.commits) + 1 == db.fail_commit:
                    raise RuntimeError("Network interrupted")
                db.commits.append(len(self.writes))
                for path, value in self.writes:
                    if value is None:
                        db.records.pop(path, None)
                    else:
                        db.records[path] = value

        return Batch()


class SavedLinksTests(unittest.TestCase):
    def test_many_links_are_validated_deduplicated_without_truncation(self):
        urls = [f"https://example.org/source/{i}" for i in range(1200)]
        parsed, invalid = parse_links("\n".join(urls + [urls[0], "ftp://example.org/file", "not a link"]))
        self.assertEqual(parsed, urls)
        self.assertEqual(invalid, ["ftp://example.org/file", "not a link"])

    def test_common_url_variants_do_not_create_duplicate_entries(self):
        urls, invalid = parse_links("EXAMPLE.org\nhttps://example.org/\nhttps://example.org:443/#top")
        self.assertEqual(urls, ["https://example.org/"])
        self.assertEqual(invalid, [])

    def test_large_library_persists_with_bounded_writes_and_isolated_accounts(self):
        db = MemoryDB()
        urls = [f"https://example.org/{i}" for i in range(1005)]
        save_links(db, "Owner@Example.com", urls)
        self.assertEqual(db.commits, [400, 400, 205])
        self.assertEqual(set(load_saved_links(db, " owner@example.com ")), set(urls))
        self.assertEqual(load_saved_links(db, "other@example.com"), [])
        save_links(db, "other@example.com", urls[:1])
        remove_saved_links(db, "owner@example.com", urls[:1])
        self.assertEqual(len(load_saved_links(db, "owner@example.com")), 1004)
        self.assertEqual(load_saved_links(db, "other@example.com"), urls[:1])

    def test_partial_save_can_be_retried_without_duplicates(self):
        db = MemoryDB()
        urls = [f"https://example.org/{i}" for i in range(450)]
        db.fail_commit = 2
        with self.assertRaises(RuntimeError):
            save_links(db, "owner@example.com", urls)
        self.assertEqual(len(db.records), 400)
        db.fail_commit = None
        save_links(db, "owner@example.com", urls)
        self.assertEqual(len(db.records), 450)


class LinkWorkspaceTests(unittest.TestCase):
    script = (
        "from dashboard import _render_link_workspace\n"
        "import streamlit as st\n"
        "targets, valid = _render_link_workspace('owner@example.com')\n"
        "st.json(targets)\n"
    )

    def test_save_reopen_select_all_and_remove_without_repasting(self):
        db = MemoryDB()
        urls = [f"https://example.org/source/{i}" for i in range(40)]
        with patch("dashboard.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.text_area[0].input("\n".join(urls + urls[:1])).run()
            self.assertEqual(json.loads(app.json[0].value), urls)
            app.button(key="save_source_links").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.text_area[0].value, "")
            self.assertEqual(len(app.multiselect[0].value), 40)
            self.assertEqual(len(db.records), 40)

            # A new session reads its sources from persistent storage.
            reopened = AppTest.from_string(self.script).run()
            self.assertEqual(len(reopened.multiselect[0].options), 40)
            self.assertEqual(reopened.text_area[0].value, "")
            reopened.button(key="select_all_links").click().run()
            self.assertEqual(set(json.loads(reopened.json[0].value)), set(urls))
            reopened.button(key="remove_selected_links").click().run()
            self.assertFalse(list(reopened.exception))
            self.assertEqual(reopened.multiselect[0].options, [])
            self.assertEqual(len(db.records), 0)

    def test_invalid_links_cannot_be_saved(self):
        db = MemoryDB()
        with patch("dashboard.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.text_area[0].input("https://example.org/\nnot a link").run()
            app.button(key="save_source_links").click().run()
            self.assertEqual(len(app.error), 1)
            self.assertEqual(db.records, {})

    def test_cambodia_shortcut_saves_and_selects_a_reusable_source(self):
        from modules.reddit import CAMBODIA_URL
        db = MemoryDB()
        with patch("dashboard.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.button(key="add_cambodia_source").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(json.loads(app.json[0].value), [CAMBODIA_URL])
            self.assertEqual(load_saved_links(db, "owner@example.com"), [CAMBODIA_URL])


class LargeScrapeTests(unittest.TestCase):
    def test_every_link_runs_with_bounded_parallelism_progress_and_error_isolation(self):
        from dashboard import _run_multi_scrape_sync

        active, peak = 0, 0
        urls = [f"https://example.org/{i}" for i in range(32)]
        calls, progress = [], []

        async def scrape(target_url, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            calls.append(target_url)
            try:
                await asyncio.sleep(0.001)
                if target_url == urls[5]:
                    raise RuntimeError("One unavailable source")
                return [{"url": target_url, "raw_text": "post"}]
            finally:
                active -= 1

        with patch("dashboard.scrape_group", side_effect=scrape):
            rows = _run_multi_scrape_sync(
                urls, headless=True, scroll_times=10,
                on_progress=lambda done, total, url: progress.append((done, total)),
            )
        self.assertEqual(set(calls), set(urls))
        self.assertEqual(peak, 3)
        self.assertEqual([row["target_url"] for row in rows], urls)
        self.assertEqual(rows[5]["source_type"], "batch_error")
        self.assertEqual(progress, [(i, 32) for i in range(1, 33)])


if __name__ == "__main__":
    unittest.main()
