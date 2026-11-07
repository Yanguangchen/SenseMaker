"""Source cards route to collectors without losing drafts or starting collection."""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from modules.scrape_navigation import FACEBOOK_USAGE_WARNING


class ScrapeNavigationTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("dashboard.IS_CLOUD", False))
        stack.enter_context(patch("dashboard.init_db"))
        stack.enter_context(patch("modules.database.init_db"))
        stack.enter_context(patch("modules.facebook_links_ui.init_db"))
        stack.enter_context(patch("modules.auth.verify_access", return_value="owner@example.com"))
        stack.enter_context(patch("modules.auth.allowed_emails", return_value={"owner@example.com"}))
        self.collectors = [stack.enter_context(patch(f"dashboard.{name}")) for name in
                           ("collect_news", "collect_x", "collect_websites", "_run_multi_scrape_sync")]
        self.app = AppTest.from_string(
            "from dashboard import _render_scrape_workspace\n"
            "from modules.facebook_session import FacebookSession\n"
            "_render_scrape_workspace('owner@example.com', FacebookSession())"
        ).run()
        self.assertFalse(self.app.exception)

    def test_five_main_cards_and_reddit_under_other_sources_do_not_start_collection(self):
        self.assertEqual([tab.label for tab in self.app.tabs], ["Collect data", "Saved Facebook links"])
        self.assertNotIn("scrape_inspect_facebook_links", [button.key for button in self.app.button])
        self.assertIsNone(self.app.session_state["scrape_source"])
        self.assertEqual([button.label for button in self.app.button
                          if button.key.startswith("scrape_open_") and button.key != "scrape_open_reddit"],
                         ["Open Facebook →", "Open Oyoshi on Facebook →", "Open X →", "Open Online news →", "Open Custom website →"])
        other_sources = next(expander for expander in self.app.expander if expander.label == "Other sources")
        self.assertEqual([button.label for button in other_sources.button], ["Open Reddit"])
        self.assertNotIn(FACEBOOK_USAGE_WARNING, [warning.value for warning in self.app.warning])
        self.assertEqual(self.app.button(key="scrape_open_facebook").proto.help, FACEBOOK_USAGE_WARNING)
        for collector in self.collectors:
            collector.assert_not_called()

    def test_usage_warning_is_a_tooltip_in_original_facebook_flow_but_not_oyoshi(self):
        self.app.button(key="scrape_open_facebook").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn(FACEBOOK_USAGE_WARNING, [warning.value for warning in self.app.warning])
        self.assertEqual(next(header for header in self.app.subheader if header.value == "Facebook").proto.help,
                         FACEBOOK_USAGE_WARNING)
        self.app.button(key="scrape_all_sources").click().run()
        self.app.run()
        self.app.button(key="scrape_open_facebook_search").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn(FACEBOOK_USAGE_WARNING, [warning.value for warning in self.app.warning])
        self.assertNotIn(FACEBOOK_USAGE_WARNING, [header.proto.help for header in self.app.subheader])

    def test_switching_sources_preserves_news_and_reddit_settings_and_collected_results(self):
        self.app.button(key="scrape_open_news").click().run()
        query = next(field for field in self.app.text_input if field.key.startswith("news_query_"))
        self.app.text_input(key=query.key).set_value("My saved news search").run()
        snapshot = {"id": "saved-run", "rows": [{"title": "Unsaved result"}]}
        self.app.session_state["last_collected_dataset"] = snapshot
        self.app.button(key="scrape_all_sources").click().run()
        self.app.button(key="scrape_open_reddit").click().run()
        self.app.number_input(key="reddit_post_limit").set_value(75).run()
        self.app.button(key="scrape_all_sources").click().run()
        self.app.button(key="scrape_open_news").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["scrape_source"], "news")
        self.assertEqual(self.app.text_input(key=query.key).value, "My saved news search")
        self.assertEqual(self.app.number_input(key="reddit_post_limit").value, 75)
        self.assertEqual(self.app.session_state["last_collected_dataset"], snapshot)
        for collector in self.collectors:
            collector.assert_not_called()

    def test_x_and_website_cards_open_their_steps_and_restore_drafts(self):
        self.app.button(key="scrape_open_x").click().run()
        self.assertTrue(self.app.session_state["x_focus_open"])
        self.app.text_input(key="x_query_oyoshi").set_value("My X draft").run()
        self.app.button(key="scrape_all_sources").click().run()
        self.app.run()  # Streamlit 1.50 can keep dismissed dialog nodes for one run.
        self.assertFalse(self.app.session_state["x_focus_open"])
        self.app.button(key="scrape_open_website").click().run()
        self.assertEqual(self.app.session_state["focus_workspace_modal"], "website")
        self.app.text_area(key="website_links").set_value("https://example.com/article").run()
        self.app.button(key="scrape_all_sources").click().run()
        self.app.run()
        self.app.button(key="scrape_open_x").click().run()
        self.assertEqual(self.app.text_input(key="x_query_oyoshi").value, "My X draft")
        self.app.button(key="scrape_all_sources").click().run()
        self.app.run()
        self.app.button(key="scrape_open_website").click().run()
        self.assertEqual(self.app.text_area(key="website_links").value, "https://example.com/article")
        self.assertFalse(self.app.exception)
        for collector in self.collectors:
            collector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
