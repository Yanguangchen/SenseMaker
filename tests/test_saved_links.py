"""Persistent libraries, account isolation, bulk operations and large scrape jobs."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules.saved_links import load_saved_links, parse_links, remove_saved_links, save_links
from modules.reddit import CAMBODIA_URL


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

    def test_auto_save_reopen_select_all_and_remove_without_repasting(self):
        db = MemoryDB()
        urls = [f"https://example.org/source/{i}" for i in range(40)]
        with patch("dashboard.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.text_area[0].input("\n".join(urls + urls[:1])).run()
            self.assertEqual(json.loads(app.json[0].value), [CAMBODIA_URL] + urls)
            self.assertFalse(any(button.label == "Save links" for button in app.button))
            self.assertFalse(list(app.exception))
            self.assertEqual(app.text_area[0].value, "")
            self.assertEqual(len(app.multiselect[0].value), 41)
            self.assertEqual(len(db.records), 40)

            # A new session reads its sources from persistent storage.
            reopened = AppTest.from_string(self.script).run()
            self.assertEqual(len(reopened.multiselect[0].options), 41)
            self.assertEqual(reopened.text_area[0].value, "")
            reopened.button(key="select_all_links").click().run()
            self.assertEqual(set(json.loads(reopened.json[0].value)), set([CAMBODIA_URL] + urls))
            reopened.button(key="remove_selected_links").click().run()
            self.assertFalse(list(reopened.exception))
            self.assertEqual(reopened.multiselect[0].options, [CAMBODIA_URL])
            self.assertEqual(reopened.multiselect[0].value, [CAMBODIA_URL])
            self.assertEqual(len(db.records), 0)

    def test_invalid_links_cannot_be_saved(self):
        db = MemoryDB()
        with patch("dashboard.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.text_area[0].input("https://example.org/\nnot a link").run()
            self.assertEqual(len(app.error), 1)
            self.assertEqual(db.records, {})

    def test_pasting_then_collecting_saves_links_without_losing_the_collect_click(self):
        db = MemoryDB()
        script = self.script + (
            "if st.button('Collect posts', key='collect_posts'):\n"
            "    st.session_state['collected_targets'] = targets\n"
        )
        with patch("dashboard.init_db", return_value=db):
            app = AppTest.from_string(script).run()
            app.text_area[0].input("https://example.org/new")
            app.button(key="collect_posts").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state["collected_targets"],
                             [CAMBODIA_URL, "https://example.org/new"])
            self.assertEqual(len(db.records), 1)

    def test_clear_deselects_all_sources_and_clears_pasted_links_without_deleting_saved_links(self):
        db = MemoryDB()
        urls = ["https://example.org/first", "https://example.org/second"]
        with patch("dashboard.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            app.text_area[0].input("\n".join(urls)).run()
            app.text_area[0].input("https://example.org/unsaved\nnot a link").run()
            app.button(key="clear_selected_links").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.multiselect[0].value, [])
            self.assertEqual(app.text_area[0].value, "")
            self.assertEqual(json.loads(app.json[0].value), [])
            self.assertEqual(len(app.error), 0)
            self.assertEqual(len(db.records), 2)
            self.assertEqual(app.multiselect[0].options, [CAMBODIA_URL] + urls)

            # Pasted links also make Clear available when nothing is selected.
            app.text_area[0].input("https://example.org/unsaved\nnot a link").run()
            self.assertFalse(app.button(key="clear_selected_links").disabled)
            app.button(key="clear_selected_links").click().run()
            app.button(key="refresh_saved_links").click().run()
            self.assertEqual(json.loads(app.json[0].value), [])
            self.assertEqual(app.text_area[0].value, "")
            self.assertEqual(len(db.records), 2)

    def test_cambodia_is_selected_by_default_and_can_be_deselected(self):
        db = MemoryDB()
        with patch("dashboard.init_db", return_value=db):
            app = AppTest.from_string(self.script).run()
            self.assertFalse(list(app.exception))
            self.assertEqual(json.loads(app.json[0].value), [CAMBODIA_URL])
            self.assertFalse(any(button.key == "add_cambodia_source" for button in app.button))
            app.button(key="clear_selected_links").click().run()
            app.button(key="refresh_saved_links").click().run()
            self.assertEqual(json.loads(app.json[0].value), [])
            self.assertEqual(app.multiselect[0].options, [CAMBODIA_URL])


class SourceWorkspaceTests(unittest.TestCase):
    script = (
        "import streamlit as st\n"
        "from dashboard import _render_collection_workspace\n"
        "from modules.facebook_session import FacebookSession\n"
        "facebook, reddit = st.tabs(['Facebook', 'Reddit'])\n"
        "with facebook:\n"
        "    _render_collection_workspace('facebook', 'owner@example.com', FacebookSession('connected', '123', 'Editor'))\n"
        "with reddit:\n"
        "    _render_collection_workspace('reddit', 'owner@example.com', FacebookSession())\n"
    )

    def test_sources_collection_results_and_clear_stay_in_their_own_tabs(self):
        db = MemoryDB()
        facebook_url = "https://www.facebook.com/groups/community"
        save_links(db, "owner@example.com", [facebook_url, CAMBODIA_URL])

        def collect(**kwargs):
            return [{"url": url, "raw_text": url, "source_type": "post"} for url in kwargs["urls"]]

        with patch("dashboard.init_db", return_value=db), \
             patch("dashboard.reddit_configured", return_value=True), \
             patch("dashboard._run_multi_scrape_sync", side_effect=collect) as scrape:
            app = AppTest.from_string(self.script).run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.multiselect[0].options, [facebook_url])
            self.assertEqual(len(app.multiselect), 1)
            self.assertFalse(list(app.tabs[1].multiselect))
            self.assertFalse(list(app.tabs[1].text_area))
            self.assertNotIn("Your links", [heading.value for heading in app.tabs[1].subheader])
            app.button(key="select_all_links_facebook").click().run()
            app.button(key="collect_facebook_posts").click().run()
            self.assertEqual(scrape.call_args.kwargs["urls"], [facebook_url])
            app.number_input(key="reddit_post_limit").set_value(100).run()
            app.button(key="collect_reddit_posts").click().run()
            self.assertEqual(scrape.call_args.kwargs["urls"], [CAMBODIA_URL])
            self.assertEqual(scrape.call_args.kwargs["reddit_limit"], 100)
            self.assertEqual(app.session_state["last_scrape_results"][0]["url"], facebook_url)
            self.assertEqual(app.session_state["last_reddit_scrape_results"][0]["url"], CAMBODIA_URL)
            self.assertTrue(any(button.key == "save_last_results_btn" for button in app.button))
            self.assertTrue(any(button.key == "save_reddit_results_btn" for button in app.button))
            app.button(key="clear_selected_links_facebook").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.multiselect[0].value, [])
            self.assertFalse(list(app.tabs[1].multiselect))
            self.assertEqual(len(db.records), 2)

    def test_other_platform_links_cannot_be_saved_or_collected_in_facebook(self):
        db = MemoryDB()
        with patch("dashboard.init_db", return_value=db), \
             patch("dashboard._run_multi_scrape_sync") as scrape:
            app = AppTest.from_string(self.script).run()
            app.text_area[0].input(CAMBODIA_URL).run()
            app.button(key="collect_facebook_posts").click().run()
            self.assertFalse(list(app.exception))
            self.assertTrue(any("Use valid Facebook links" in error.value for error in app.error))
            self.assertEqual(db.records, {})
            scrape.assert_not_called()


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
