"""Direct Oyoshi search routing, session reuse and bounded collection."""

import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

from streamlit.testing.v1 import AppTest

from modules.collection_jobs import CollectionJob
from modules.facebook_search import OYOSHI_SEARCH_URL, validate_facebook_search_url
from tests.test_saved_links import MemoryDB


class FacebookSearchURLTests(unittest.TestCase):
    def test_filtered_search_url_keeps_query_and_filters(self):
        url = "https://www.facebook.com/search/posts/?q=Oyoshi&filters=eyJycF9jcmVhdGlvbl90aW1lIjoieCJ9%3D"
        self.assertEqual(validate_facebook_search_url(url), url)

    def test_non_search_and_external_urls_are_rejected(self):
        for url in ("", "javascript:alert(1)", "https://facebook.com/groups/oyoshi",
                    "https://facebook.com/search/posts/?q=", "https://facebook.com.evil.test/search/posts/?q=oyoshi",
                    "https://user:password@facebook.com/search/posts/?q=oyoshi"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_facebook_search_url(url)


class FacebookSearchUITests(unittest.TestCase):
    script = (
        "from dashboard import _render_scrape_workspace\n"
        "from modules.facebook_session import FacebookSession\n"
        "_render_scrape_workspace('owner@example.com', FacebookSession('connected', '123', 'Editor'))\n"
    )

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("dashboard.IS_CLOUD", False))
        self.stack.enter_context(patch("dashboard.init_db", return_value=MemoryDB()))
        self.stack.enter_context(patch("modules.facebook_links_ui.init_db", return_value=MemoryDB()))
        self.stack.enter_context(patch("modules.auth.verify_access", return_value="owner@example.com"))
        self.stack.enter_context(patch("modules.auth.allowed_emails", return_value={"owner@example.com"}))
        self.collect = self.stack.enter_context(patch("dashboard._run_multi_scrape_sync", return_value=[{
            "url": "https://www.facebook.com/example/posts/123", "raw_text": "Oyoshi news",
            "source_type": "container_post",
        }]))
        self.links = self.stack.enter_context(patch("dashboard._render_link_workspace", return_value=(
            ["https://www.facebook.com/groups/original"], True)))

    def open_search(self):
        app = AppTest.from_string(self.script).run()
        app.button(key="scrape_open_facebook_search").click().run()
        self.assertFalse(app.exception)
        return app

    def test_card_skips_link_selection_and_collects_direct_url_with_three_scrolls(self):
        app = self.open_search()
        self.links.assert_not_called()
        self.collect.assert_not_called()
        self.assertEqual(app.text_input(key="facebook_search_url").value, OYOSHI_SEARCH_URL)
        self.assertEqual(app.slider(key="facebook_search_scroll_times").value, 3)
        self.assertFalse(app.button(key="collect_facebook_posts").disabled)
        self.assertNotIn("facebook_search_wizard_step_next", [button.key for button in app.button])
        self.assertNotIn("facebook_search_wizard_step_previous", [button.key for button in app.button])
        app.button(key="collect_facebook_posts").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.collect.call_args.kwargs["urls"], [OYOSHI_SEARCH_URL])
        self.assertEqual(self.collect.call_args.kwargs["scroll_times"], 3)
        self.assertTrue(self.collect.call_args.kwargs["strict_scroll_limit"])
        self.assertEqual(app.session_state["last_scrape_urls"], [OYOSHI_SEARCH_URL])
        self.assertEqual(app.session_state["last_collected_dataset"]["source"], "Facebook")
        self.assertTrue(any(button.key == "save_last_results_btn" for button in app.button))

    def test_search_filters_and_two_scrolls_survive_switching_without_replacing_normal_links(self):
        app = self.open_search()
        url = OYOSHI_SEARCH_URL + "&filters=eyJleGFtcGxlIjp0cnVlfQ%3D%3D"
        app.text_input(key="facebook_search_url").input(url).run()
        app.slider(key="facebook_search_scroll_times").set_value(2).run()
        app.button(key="scrape_all_sources").click().run()
        app.run()
        app.button(key="scrape_open_facebook").click().run()
        self.links.assert_called_once()
        self.assertEqual(app.session_state["facebook_wizard_targets"], ["https://www.facebook.com/groups/original"])
        app.button(key="facebook_wizard_step_next").click().run()
        self.assertEqual(app.slider(key="facebook_scroll_times").value, 10)
        app.button(key="scrape_all_sources").click().run()
        app.run()
        app.button(key="scrape_open_facebook_search").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key="facebook_search_url").value, url)
        self.assertEqual(app.slider(key="facebook_search_scroll_times").value, 2)
        app.button(key="collect_facebook_posts").click().run()
        self.assertEqual(self.collect.call_args.kwargs["urls"], [url])
        self.assertEqual(self.collect.call_args.kwargs["scroll_times"], 2)

    def test_invalid_search_link_disables_collection(self):
        app = self.open_search()
        app.text_input(key="facebook_search_url").input("https://example.com/search?q=oyoshi").run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertTrue(app.button(key="collect_facebook_posts").disabled)
        self.collect.assert_not_called()

    def test_hosted_search_uses_existing_worker_and_owner_with_strict_scroll_limit(self):
        job = CollectionJob("oyoshi-job", (OYOSHI_SEARCH_URL,))
        started = False
        run_collection = None

        def start(email, urls, run):
            nonlocal started, run_collection
            self.assertEqual(email, "owner@example.com")
            self.assertEqual(urls, [OYOSHI_SEARCH_URL])
            started, run_collection = True, run
            return job.id

        with patch("dashboard.IS_CLOUD", True), \
             patch("dashboard.active_collection", side_effect=lambda email: job if started else None), \
             patch("dashboard.collection_job", return_value=job), \
             patch("dashboard.start_collection", side_effect=start) as launch, \
             patch("dashboard._run_multi_scrape_async", new_callable=AsyncMock, return_value=[]) as run:
            app = self.open_search()
            app.button(key="collect_facebook_posts").click().run()
            self.assertFalse(app.exception)
            launch.assert_called_once()
            self.assertTrue(app.button(key="collect_facebook_posts").disabled)
            self.assertFalse(app.button(key="cancel_facebook_collection").disabled)
            activity = Mock()
            asyncio.run(run_collection(Mock(), Mock(), activity))
            self.assertIs(run.call_args.kwargs["on_activity"], activity)
            self.assertEqual(run.call_args.kwargs["facebook_owner"], "owner@example.com")
            self.assertEqual(run.call_args.kwargs["scroll_times"], 3)
            self.assertTrue(run.call_args.kwargs["strict_scroll_limit"])
            self.assertTrue(run.call_args.kwargs["headless"])

    def test_unconnected_user_is_sent_to_facebook_login_first(self):
        script = self.script.replace("FacebookSession('connected', '123', 'Editor')", "FacebookSession()")
        with patch("dashboard.IS_CLOUD", True), \
             patch("dashboard.active_collection", return_value=None), \
             patch("dashboard.remote_facebook_configured", return_value=True):
            app = AppTest.from_string(script).run()
            app.button(key="scrape_open_facebook_search").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.button(key="capture_fb_session_btn").disabled)
            self.assertNotIn("collect_facebook_posts", [button.key for button in app.button])
            self.collect.assert_not_called()


    def test_scroll_slider_allows_up_to_five_scrolls(self):
        app = self.open_search()
        app.slider(key="facebook_search_scroll_times").set_value(5).run()
        self.assertEqual(app.slider(key="facebook_search_scroll_times").value, 5)
        app.button(key="collect_facebook_posts").click().run()
        self.assertEqual(self.collect.call_args.kwargs["scroll_times"], 5)

    def test_collected_screen_hides_next_and_previous_and_save_closes_window(self):
        app = self.open_search()
        app.button(key="collect_facebook_posts").click().run()
        self.assertFalse(app.exception)
        # Next and previous are hidden after posts are collected:
        self.assertNotIn("facebook_search_wizard_step_previous", [button.key for button in app.button])
        self.assertNotIn("facebook_search_wizard_step_next", [button.key for button in app.button])
        self.assertTrue(any(button.key == "save_last_results_btn" for button in app.button))
        self.assertEqual(app.session_state.get("focus_workspace_modal"), "facebook")

        # Blank title keeps window open with error:
        app.button(key="save_last_results_btn").click().run()
        self.assertTrue(app.error)
        self.assertEqual(app.session_state.get("focus_workspace_modal"), "facebook")

        # Firestore failure keeps window open:
        app.text_input(key="custom_title_value").set_value("Oyoshi dataset").run()
        with patch("dashboard._write_results_to_firestore", side_effect=RuntimeError("write failed")):
            app.button(key="save_last_results_btn").click().run()
        self.assertTrue(any("Firestore write failed: write failed" in e.value for e in app.error))
        self.assertEqual(app.session_state.get("focus_workspace_modal"), "facebook")

        # Successful save closes window and shows confirmation on workspace:
        with patch("dashboard._write_results_to_firestore", return_value={"inserted": 1, "skipped": 0}):
            app.button(key="save_last_results_btn").click().run()
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state.get("focus_workspace_modal"))
        self.assertTrue(any("Saved to Firestore" in s.value for s in app.success))


class FacebookSearchCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_passes_strict_limit_to_browser_collector(self):
        from dashboard import _run_multi_scrape_async

        with patch("dashboard.scrape_group", new_callable=AsyncMock, return_value=[]) as collect:
            await _run_multi_scrape_async([OYOSHI_SEARCH_URL], headless=True, scroll_times=2,
                                         facebook_owner="owner@example.com", strict_scroll_limit=True)
        self.assertEqual(collect.call_args.kwargs["target_url"], OYOSHI_SEARCH_URL)
        self.assertEqual(collect.call_args.kwargs["scroll_min"], 2)
        self.assertEqual(collect.call_args.kwargs["scroll_max"], 2)
        self.assertTrue(collect.call_args.kwargs["strict_scroll_limit"])

    async def test_source_activity_reports_live_counts_before_source_completion(self):
        from dashboard import _run_multi_scrape_async

        events = []
        async def collect(target_url, on_progress, **kwargs):
            on_progress("Opening source", 0)
            on_progress("Scroll 1 of 2", 3)
            self.assertEqual(events[-1], ("activity", target_url, "Scroll 1 of 2", 3))
            self.assertFalse(any(event[0] == "finished" for event in events))
            return [{"url": f"{target_url}#post-{index}", "source_type": "container_post"} for index in range(3)] + [
                {"url": target_url, "source_type": "batch_warning"}]

        with patch("dashboard.scrape_group", side_effect=collect):
            await _run_multi_scrape_async(
                [OYOSHI_SEARCH_URL], headless=True, scroll_times=2,
                on_activity=lambda *args: events.append(("activity", *args)),
                on_progress=lambda *args: events.append(("finished", *args)),
            )
        self.assertEqual(events[-2], ("activity", OYOSHI_SEARCH_URL, "Source finished", 3))
        self.assertEqual(events[-1], ("finished", 1, 1, OYOSHI_SEARCH_URL))


class LocalCollectionProgressTests(unittest.TestCase):
    def test_local_collection_renders_activity_and_clears_running_status_on_completion(self):
        from dashboard import _show_collection_progress

        snapshots = []
        def render(completed, total, activity, started_at):
            snapshots.append((completed, total, dict(activity)))
            _show_collection_progress(completed, total, activity, started_at)

        async def collect(target_url, on_progress, **kwargs):
            on_progress("Scroll 1 of 2", 1)
            return [{"url": target_url, "raw_text": "Collected post", "source_type": "container_post"}]

        script = (
            "from dashboard import _render_collect_controls\n"
            "from modules.facebook_session import FacebookSession\n"
            f"_render_collect_controls('facebook', 'owner@example.com', [{OYOSHI_SEARCH_URL!r}], True, "
            "FacebookSession('connected', '123'), use_headless=True, scroll_times=2, "
            "reddit_limit=50, reddit_comments=20)\n"
        )
        with patch("dashboard.IS_CLOUD", False), patch("dashboard.scrape_group", side_effect=collect), \
             patch("dashboard._show_collection_progress", side_effect=render):
            app = AppTest.from_string(script).run()
            app.button(key="collect_facebook_posts").click().run()
            self.assertFalse(app.exception)
            self.assertIn((0, 1, {OYOSHI_SEARCH_URL: ("Scroll 1 of 2", 1)}), snapshots)
            self.assertEqual(snapshots[-1], (1, 1, {OYOSHI_SEARCH_URL: ("Source finished", 1)}))
            self.assertFalse(app.get("progress"))
            self.assertFalse(app.button(key="collect_facebook_posts").disabled)
            self.assertEqual(len(app.session_state["last_scrape_results"]), 1)
            self.assertIn("save_last_results_btn", [button.key for button in app.button])


if __name__ == "__main__":
    unittest.main()
