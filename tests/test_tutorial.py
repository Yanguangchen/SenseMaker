"""End-to-end coverage for the interactive tutorial workspace, CLI batch entrypoint, and modal auth gates."""

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from streamlit.testing.v1 import AppTest

import main as cli_main
from modules.tutorial_ui import (
    GUIDE_PATH,
    chapter_subsections,
    guide_sections,
    search_preview,
    search_sections,
)


class TutorialWorkspaceTests(unittest.TestCase):
    script = "from modules.tutorial_ui import render_tutorial_workspace\nrender_tutorial_workspace()\n"

    def test_guide_sections_and_multiword_search(self):
        sections = guide_sections(GUIDE_PATH.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(sections), 10)
        matches = search_sections(sections, "sentiment filter sources")
        self.assertTrue(any("Sentiment Analysis" in title for title, _ in matches))
        self.assertEqual(search_sections(sections, "nonexistentkeywordxyz"), [])

    def test_chapter_subsections_and_search_preview_helpers(self):
        sections = guide_sections(GUIDE_PATH.read_text(encoding="utf-8"))
        for title, body in sections:
            subtopics = chapter_subsections(body)
            self.assertGreaterEqual(len(subtopics), 3, f"Expected subtopics in chapter: {title}")
        sample_body = (
            "Intro paragraph about Facebook groups.\n\n"
            "### Step modal\n\n"
            "| Col | Value |\n| --- | --- |\n| A | B |\n\n"
            "> **Tip:** Use Oyoshi Japan Campaign for campaign discovery."
        )
        self.assertEqual(chapter_subsections(sample_body), ["Step modal"])
        self.assertIn("Oyoshi Japan Campaign", search_preview(sample_body, "Oyoshi"))
        self.assertIn("Facebook groups", search_preview(sample_body, ""))

    def test_chapter_navigation_search_and_read_chapter_flow(self):
        app = AppTest.from_string(self.script).run()
        self.assertFalse(list(app.exception))
        titles = [title for title, _ in guide_sections(GUIDE_PATH.read_text(encoding="utf-8"))]
        self.assertEqual(app.radio(key="tutorial_topic").value, titles[0])
        self.assertTrue(app.button(key="tutorial_previous").disabled)
        self.assertFalse(app.button(key="tutorial_next").disabled)

        # Advance to the next chapter and return to the first chapter:
        app.button(key="tutorial_next").click().run()
        self.assertEqual(app.radio(key="tutorial_topic").value, titles[1])
        self.assertFalse(app.button(key="tutorial_previous").disabled)
        app.button(key="tutorial_previous").click().run()
        self.assertEqual(app.radio(key="tutorial_topic").value, titles[0])

        # Search chapters and open a matching chapter via "Read chapter →":
        app.text_input(key="tutorial_search").set_value("GenCite").run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any("Search results" in m.value for m in app.markdown))
        gencite_index = next(i for i, title in enumerate(titles) if "GenCite" in title)
        app.button(key=f"tutorial_result_{gencite_index}").click().run()
        self.assertEqual(app.text_input(key="tutorial_search").value, "")
        self.assertEqual(app.radio(key="tutorial_topic").value, titles[gencite_index])

        # No-match search and Clear search button:
        app.text_input(key="tutorial_search").set_value("nonexistentkeywordxyz").run()
        self.assertTrue(any("No chapters match" in info.value for info in app.info))
        app.button(key="tutorial_clear_search").click().run()
        self.assertEqual(app.text_input(key="tutorial_search").value, "")

    def test_empty_guide_shows_update_notice(self):
        with patch("modules.tutorial_ui.guide_sections", return_value=[]):
            app = AppTest.from_string(self.script).run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any("being updated" in info.value for info in app.info))


class CliMainEntrypointTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_target_group_url_raises_value_error(self):
        with patch("main.load_environment"), patch.dict(os.environ, {"TARGET_GROUP_URL": ""}, clear=False):
            with self.assertRaises(ValueError):
                await cli_main.run()

    async def test_run_scrapes_persists_and_processes_pending_posts(self):
        db = MagicMock()
        async def fake_scrape(target_url, persist_cb):
            post = {"url": f"{target_url}/posts/1", "raw_text": "Hello"}
            persist_cb(post)
            return [post]

        with patch("main.load_environment"), \
             patch.dict(os.environ, {"TARGET_GROUP_URL": "https://www.facebook.com/groups/test"}, clear=False), \
             patch("main.init_db", return_value=db), \
             patch("main.upsert_post") as upsert, \
             patch("main.scrape_group", side_effect=fake_scrape) as scrape, \
             patch("main.process_pending_posts", return_value=1) as process:
            await cli_main.run()
        scrape.assert_awaited_once()
        upsert.assert_called_once_with(db, {"url": "https://www.facebook.com/groups/test/posts/1", "raw_text": "Hello"})
        process.assert_called_once_with(db, limit=25)


class DialogAuthGateTests(unittest.TestCase):
    def test_revoked_access_during_analysis_dialog_rerun_closes_modal(self):
        script = (
            "import streamlit as st\n"
            "from dashboard import _analysis_steps_modal\n"
            "if st.session_state.get('focus_workspace_modal') == 'analysis':\n"
            "    _analysis_steps_modal('editor@example.com', {'mode': 'Manual selection', 'use_recent': False, "
            "'recent': None, 'recent_ids': set(), 'datasets': [], 'sources': [], 'ready': False}, "
            "0, 'all', None, 'gemini', 'Gemini')\n"
        )
        with patch("modules.auth.verify_access", side_effect=PermissionError("revoked")), \
             patch("modules.auth.allowed_emails", return_value={"editor@example.com"}):
            app = AppTest.from_string(script, default_timeout=30)
            app.session_state["focus_workspace_modal"] = "analysis"
            app.run()
        self.assertFalse(list(app.exception))
        self.assertNotIn("focus_workspace_modal", app.session_state)


if __name__ == "__main__":
    unittest.main()
