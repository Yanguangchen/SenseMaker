"""Exercise the real DOM extractor against isolated Facebook-shaped pages."""

import unittest
from contextlib import asynccontextmanager
import time
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from modules.facebook_session import FacebookLoginError
from modules.velocity_scraper import VIEW_LABELS_JS, parse_views, read_post_views
from modules import velocity_jobs as jobs, velocity_tracking as store
from modules.velocity_table import velocity_card_sheet
from tests import test_velocity_tracking as fixtures


URL = "https://www.facebook.com/example/posts/123"


class ViewCountTests(unittest.TestCase):
    def test_only_explicit_unambiguous_view_counts_are_used(self):
        self.assertEqual(parse_views(["1.2K views", "Views: 1,200"])[0], 1200)
        self.assertEqual(parse_views(["0 views"])[0], 0)
        self.assertEqual(parse_views(["3M video views"])[0], 3_000_000)
        self.assertEqual(parse_views(["1.2K views · 45 reactions · 8 comments"])[0], 1200)
        self.assertEqual(parse_views(["45 reactions\n1.2K views\n8 comments"])[0], 1200)
        self.assertEqual(parse_views(["Views:\n1,200"])[0], 1200)
        for labels in (["12 likes", "3 comments", "50 plays"], ["We got 1.2K views today"],
                       ["100 views", "200 views"], [], ["views"]):
            self.assertEqual(parse_views(labels), (None, ""))


class ScrapeRunIntegrationTests(fixtures.VelocityTestCase):
    def test_worker_scrapes_two_posts_and_persists_two_distinct_checks(self):
        email = "scrape-test@example.com"
        card = store.add_card(self.db, email, url=URL)[0]
        card = store.add_post(self.db, email, card["id"], fixtures.post_url(456))[0]
        jobs._jobs.clear()
        self.addCleanup(jobs._jobs.clear)
        run_index, closed, visited = 0, [], []

        @asynccontextmanager
        async def browser_context(playwright, owner, **options):
            self.assertEqual(owner, email)
            self.assertTrue(options["check_before_use"])
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()

            async def route_post(route):
                url = route.request.url
                visited.append(url)
                label = ("1.2K views" if run_index == 0 else "1.6K views") if url.endswith("/123") else (
                    "640 views" if run_index == 0 else "800 views")
                await route.fulfill(content_type="text/html", body=(
                    f'<article role="article"><a href="{url}">Requested post</a><span>{label}</span>'
                    '<span>999 comments</span></article>'
                    '<article role="article"><a href="/other/posts/999">Other post</a><span>9M views</span></article>'))

            await context.route("**/*", route_post)
            try:
                yield context
            finally:
                await browser.close()
                closed.append(True)

        with patch("modules.velocity_jobs.database.init_db", return_value=self.db), \
                patch("modules.velocity_jobs.active_collection", return_value=None), \
                patch("modules.velocity_scraper.remote_facebook_context", side_effect=browser_context):
            for run_index in range(2):
                job = jobs.start_run(email, card["id"], hosted=True)
                deadline = time.monotonic() + 20
                while job.status in {"running", "saving"} and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertEqual(job.status, "complete", job.error)
        saved = store.load_cards(self.db, email)[0]
        self.assertEqual(saved["run_count"], 2)
        self.assertEqual([[r["views"] for r in run["results"]] for run in saved["runs"]], [[1200, 640], [1600, 800]])
        self.assertEqual(len([key for key in self.db.rows if "/runs/" in key]), 2)
        self.assertEqual(len(visited), 4)
        self.assertEqual(len(closed), 2)
        self.assertEqual(velocity_card_sheet(saved).count('>alive</span>'), 2)


class ViewDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def load(self, body, url=URL):
        await self.page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body))
        await self.page.goto(url)

    async def test_requested_post_excludes_recommendations_comments_and_message_quotes(self):
        await self.load(f'''<main role="main">
            <article role="article"><a href="/example/posts/999">Recommendation</a><span>90M views</span></article>
            <article role="article"><a href="{URL}">Requested post</a>
                <div data-ad-preview="message"><span>100K views</span></div>
                <span>1.2K views</span>
                <article role="article"><a href="{URL}?comment_id=4">Comment</a><span>500 views</span></article>
                <div data-testid="comment"><span>600 views</span></div>
                <span style="display:none">900 views</span>
            </article></main>''')
        result = await self.page.evaluate(VIEW_LABELS_JS, URL)
        self.assertTrue(result["matched"])
        self.assertEqual(parse_views(result["labels"])[0], 1200)
        # The production reader uses the same attribution and returns views only.
        result = await read_post_views(self.page, URL)
        self.assertEqual(result["views"], 1200)
        self.assertNotIn("comments", result)

    async def test_wrong_post_or_ambiguous_page_never_supplies_a_count(self):
        await self.load('<main role="main"><a href="/example/posts/999">Other</a><span>900 views</span></main>')
        self.assertEqual(await self.page.evaluate(VIEW_LABELS_JS, URL), {"matched": False, "labels": []})
        await self.page.goto("https://www.facebook.com/example/posts/999")
        self.assertEqual(await self.page.evaluate(VIEW_LABELS_JS, URL), {"matched": False, "labels": []})

    async def test_reel_can_use_its_single_video_container(self):
        url = "https://www.facebook.com/reel/123"
        await self.load('<main role="main"><video></video><span aria-label="42K views">42K</span></main>', url)
        result = await self.page.evaluate(VIEW_LABELS_JS, url)
        self.assertEqual(parse_views(result["labels"])[0], 42_000)
        result = await self.page.evaluate(VIEW_LABELS_JS, "https://www.facebook.com/share/r/abc")
        self.assertEqual(parse_views(result["labels"])[0], 42_000)

    async def test_share_page_can_resolve_its_canonical_post_and_split_count_label(self):
        url = "https://www.facebook.com/share/p/token"
        await self.load(f'<link rel="canonical" href="{URL}"><article role="article">'
                        f'<a href="{URL}">Post</a><div><span>1.2K</span> <span>views</span></div></article>', url)
        result = await self.page.evaluate(VIEW_LABELS_JS, url)
        self.assertEqual(parse_views(result["labels"])[0], 1200)

    async def test_missing_count_is_unavailable_and_login_redirect_is_actionable(self):
        await self.load(f'<article role="article"><a href="{URL}">Post</a><span>900 likes</span></article>')
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_views(self.page, URL)
        self.assertIsNone(result["views"])
        self.assertEqual(result["outcome"], "unavailable")
        # A lost login never becomes a zero-view result.
        page = AsyncMock()
        page.url = "https://www.facebook.com/checkpoint/123"
        with self.assertRaisesRegex(FacebookLoginError, "Reconnect Facebook"):
            await read_post_views(page, URL)

    async def test_photo_viewer_is_identified_without_a_permalink_and_ignores_comments(self):
        url = "https://www.facebook.com/photo?fbid=1906718530693623"
        await self.load('''<main role="main"><div role="dialog">
            <img width="600" height="400" alt="Photo">
            <div data-ad-preview="message">We reached <span>9M views</span></div>
            <div>1.2K views · 45 reactions · 8 comments</div>
            <article role="article" aria-label="Comment by Someone"><span>900 views</span></article>
            <article role="article"><a href="?fbid=1906718530693623&comment_id=55">Comment</a>
                <span>700 views</span></article>
            </div></main><article role="article"><span>3M views</span></article>''', url)
        snapshot = await self.page.evaluate(VIEW_LABELS_JS, url)
        self.assertTrue(snapshot["matched"])
        self.assertEqual(parse_views(snapshot["labels"])[0], 1200, snapshot)
        self.assertEqual((await read_post_views(self.page, url))["views"], 1200)

    async def test_photo_without_public_views_is_distinct_from_an_unidentified_post(self):
        url = "https://www.facebook.com/photo?fbid=1906718530693623"
        await self.load('<main role="main"><img width="600" height="400" alt="Photo">'
                        '<span>25 reactions</span><span>8 comments</span></main>', url)
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_views(self.page, url)
        self.assertEqual(result["outcome"], "unavailable")
        self.assertEqual(result["reason_code"], "views_not_shown")
        self.assertIsNone(result["views"])
        await self.page.unroute("**/*")
        await self.load('<main role="main"><img width="600" height="400" alt="Photo">'
                        '<article role="article"><a href="/other/posts/999">Other post</a>'
                        '<span>90M views</span></article></main>', url)
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_views(self.page, url)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["reason_code"], "post_not_identified")
        self.assertIsNone(result["views"])

    async def test_photo_login_overlay_requires_reconnecting(self):
        url = "https://www.facebook.com/photo?fbid=1906718530693623"
        await self.load('<main role="main"><img width="600" height="400" alt="Photo">'
                        '<input type="password"><span>100 views</span></main>', url)
        with self.assertRaisesRegex(FacebookLoginError, "Reconnect Facebook"):
            await read_post_views(self.page, url)


if __name__ == "__main__":
    unittest.main()
