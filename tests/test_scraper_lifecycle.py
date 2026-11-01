import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from modules import scraper


class ScraperLifecycleTests(unittest.IsolatedAsyncioTestCase):
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
