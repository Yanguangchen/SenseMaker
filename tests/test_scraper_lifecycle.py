import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from modules import scraper


class ScraperLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_scroll_limit_stops_at_two_or_three_even_if_more_posts_keep_loading(self):
        from modules.facebook_search import OYOSHI_SEARCH_URL

        for limit in (2, 3):
            with self.subTest(limit=limit):
                page = MagicMock(evaluate=AsyncMock(), mouse=MagicMock(wheel=AsyncMock()))
                context = MagicMock(new_page=AsyncMock(return_value=page))
                @asynccontextmanager
                async def remote(*args, **kwargs):
                    yield context
                sdk = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
                async def harvest(**kwargs):
                    url = f"https://www.facebook.com/example/posts/{len(kwargs['seen_urls'])}"
                    kwargs["seen_urls"].add(url)
                    return [{"url": url, "raw_text": "New Oyoshi post", "source_type": "container_post"}]
                with patch("modules.scraper.async_playwright", return_value=sdk), \
                     patch("modules.facebook_remote.remote_facebook_context", side_effect=remote), \
                     patch("modules.scraper._goto_with_resilient_wait", new_callable=AsyncMock) as goto, \
                     patch("modules.scraper._harvest_visible_posts", side_effect=harvest), \
                     patch("modules.scraper._click_expand_controls", new_callable=AsyncMock), \
                     patch("modules.scraper._wait_for_feed_settle", new_callable=AsyncMock):
                    rows = await scraper.scrape_group(OYOSHI_SEARCH_URL, scroll_min=limit, scroll_max=limit,
                                                      strict_scroll_limit=True, facebook_owner="owner@example.com")
                goto.assert_awaited_once_with(page, target_url=OYOSHI_SEARCH_URL, timeout_ms=60_000)
                self.assertEqual(page.mouse.wheel.await_count, limit)
                self.assertEqual(len(rows), limit * 2 + 1)

    async def test_stalled_collection_preserves_posts_and_releases_the_remote_context(self):
        context = MagicMock(new_page=AsyncMock())
        released = False
        @asynccontextmanager
        async def remote(*args, **kwargs):
            nonlocal released
            try:
                yield context
            finally:
                released = True
        sdk = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        post = {"url": "https://www.facebook.com/groups/test/posts/123", "raw_text": "A completed post", "source_type": "container_post"}
        async def stall(**kwargs):
            await kwargs["persist_cb"](post)
            await asyncio.Event().wait()
        with patch("modules.scraper.async_playwright", return_value=sdk), \
             patch("modules.facebook_remote.remote_facebook_context", side_effect=remote), \
             patch("modules.scraper._goto_with_resilient_wait", new_callable=AsyncMock), \
             patch("modules.scraper._harvest_visible_posts", side_effect=stall), \
             patch("modules.scraper.REMOTE_COLLECTION_SECONDS", 0.02):
            rows = await scraper.scrape_group("https://www.facebook.com/groups/test", facebook_owner="editor@example.com")
        self.assertTrue(released)
        self.assertEqual(rows[0], post)
        self.assertEqual(rows[1]["source_type"], "batch_warning")
        self.assertTrue(rows[1]["collection_stopped"])

    async def test_disappearing_post_is_skipped_without_waiting_thirty_seconds(self):
        container = MagicMock(inner_text=AsyncMock(side_effect=TimeoutError))
        containers = MagicMock(count=AsyncMock(return_value=1))
        containers.nth.return_value = container
        rows = await scraper._collect_from_container_list(containers, "https://www.facebook.com/groups/test", set(), None)
        self.assertEqual(rows, [])
        container.inner_text.assert_awaited_once_with(timeout=1500)


if __name__ == "__main__":
    unittest.main()
