"""Exercise engagement scraping and persistence using Facebook-shaped pages."""

import unittest
from contextlib import asynccontextmanager
import time
from unittest.mock import AsyncMock, patch

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from modules.facebook_session import FacebookLoginError
from modules.velocity_scraper import POST_ENGAGEMENT_JS, parse_engagement, read_post_engagement
from modules import velocity_jobs as jobs, velocity_tracking as store
from modules.velocity_metrics import METRIC
from modules.velocity_table import velocity_card_sheet
from tests import test_velocity_tracking as fixtures

URL = "https://www.facebook.com/example/posts/123"
PHOTO = "https://www.facebook.com/photo?fbid=1906718530693623"


class EngagementCountTests(unittest.TestCase):
    def test_totals_and_abbreviations_are_added_without_double_counting_reactions(self):
        for labels, expected in [
            (["1.2K likes", "30 comments"], (1200, 30, 1230)),
            (["Like, 82 reactions", "Comment, 5 comments", "Share, 22 shares"], (82, 5, 87)),
            (["Remove Like, 1.2K reactions", "Comment, 0 comments"], (1200, 0, 1200)),
            (["All reactions:\n1,234", "1,234 reactions", "Like: 1000", "Love: 234", "Comments: 12"], (1234, 12, 1246)),
            (["3M reactions · 1.5K comments · 500 shares · 9M views"], (3000000, 1500, 3001500)),
            (["No reactions yet", "No comments yet", "Be the first to comment."], (0, 0, 0)),
            (["0 likes", "View all 5 comments"], (0, 5, 5)),
            (["All reactions: 12", "10 likes", "3 comments"], (12, 3, 15)),
        ]:
            with self.subTest(labels=labels):
                result = parse_engagement(labels)
                self.assertEqual((result["reactions"], result["comments"], result["engagement"]), expected)

    def test_missing_ambiguous_and_non_metric_text_never_becomes_zero(self):
        for labels in ([], ["Like", "Comment", "Share"], ["12 reactions"], ["8 comments"],
                       ["12 likes", "13 likes", "2 comments"],
                       ["12 likes", "2 comments", "4 comments"],
                       ["We reached 12 likes", "Someone wrote 8 comments"],
                       ["100 views", "12 shares"], ["All reactions", "12", "8"]):
            with self.subTest(labels=labels):
                self.assertIsNone(parse_engagement(labels)["engagement"])


class ScrapeRunIntegrationTests(fixtures.VelocityTestCase):
    def test_worker_saves_components_and_totals_for_two_posts_across_two_runs(self):
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
                reactions = (1200 if run_index == 0 else 1600) if url.endswith("/123") else (640 if run_index == 0 else 800)
                comments = 30 if run_index == 0 else 50
                await route.fulfill(content_type="text/html", body=(
                    f'<article role="article"><a href="{url}">Requested post</a>'
                    f'<span>All reactions: {reactions}</span><span>{comments} comments</span><span>9M views</span></article>'
                    '<article role="article"><a href="/other/posts/999">Other post</a><span>9M likes</span></article>'))

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
        self.assertEqual([[r["engagement"] for r in run["results"]] for run in saved["runs"]], [[1230, 670], [1650, 850]])
        self.assertTrue(all(run["metric"] == METRIC for run in saved["runs"]))
        self.assertEqual(saved["runs"][0]["results"][0]["reactions"], 1200)
        self.assertEqual(saved["runs"][0]["results"][0]["comments"], 30)
        self.assertEqual(len([key for key in self.db.rows if "/runs/" in key]), 2)
        self.assertEqual(len(visited), 4)
        self.assertEqual(len(closed), 2)
        self.assertEqual(velocity_card_sheet(saved).count('>alive</span>'), 2)


class EngagementDOMTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_requested_post_excludes_recommendations_comment_likes_and_message_quotes(self):
        await self.load(f'''<main role="main">
            <article role="article"><a href="/example/posts/999">Recommendation</a><span>90M likes</span><span>5K comments</span></article>
            <article role="article"><a href="{URL}">Requested post</a>
                <div data-ad-preview="message"><span>100K likes</span><span>3K comments</span></div>
                <span>All reactions: 1.2K</span><span>80 comments</span><span>900 views</span><span>50 shares</span>
                <article role="article"><a href="{URL}?comment_id=4">Comment</a><span>500 likes</span></article>
                <div data-testid="comment"><span>600 likes</span></div>
                <span style="display:none">900 comments</span>
            </article></main>''')
        result = await read_post_engagement(self.page, URL)
        self.assertEqual((result["reactions"], result["comments"], result["engagement"]), (1200, 80, 1280))
        self.assertNotIn("views", result)

    async def test_wrong_post_or_ambiguous_page_never_supplies_a_count(self):
        await self.load('<main role="main"><a href="/example/posts/999">Other</a><span>900 likes</span><span>9 comments</span></main>')
        self.assertEqual(await self.page.evaluate(POST_ENGAGEMENT_JS, URL), {"matched": False, "labels": []})
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_engagement(self.page, URL)
        self.assertEqual(result["reason_code"], "post_not_identified")
        self.assertIsNone(result["engagement"])
        await self.page.goto("https://www.facebook.com/example/posts/999")
        self.assertEqual(await self.page.evaluate(POST_ENGAGEMENT_JS, URL), {"matched": False, "labels": []})

    async def test_reel_can_use_its_single_video_container(self):
        url = "https://www.facebook.com/reel/123"
        await self.load('<main role="main"><video></video><span aria-label="42K reactions">42K</span><span>5 comments</span></main>', url)
        result = await read_post_engagement(self.page, url)
        self.assertEqual(result["engagement"], 42_005)

    async def test_reel_carousel_reads_only_visible_card_and_action_labels(self):
        url = "https://www.facebook.com/reel/123"
        await self.load('''<main role="main">
            <div><video style="width:320px;height:500px"></video>
                <div role="button" aria-label="Like, 82 reactions">82</div>
                <div role="button" aria-label="Comment, 5 comments">5</div>
                <div role="button" aria-label="Share, 22 shares">22</div>
                <div data-testid="comment"><span>600 likes</span></div></div>
            <div style="position:absolute;top:1000px"><video style="width:320px;height:500px"></video>
                <div role="button" aria-label="Like, 104 reactions">104</div>
                <div role="button" aria-label="Comment, 18 comments">18</div></div>
            </main>''', url)
        result = await read_post_engagement(self.page, url)
        self.assertEqual((result["reactions"], result["comments"], result["engagement"]), (82, 5, 87))
        self.assertEqual(await self.page.locator('[data-sm-velocity-scope] video').count(), 1)

    async def test_reel_carousel_does_not_borrow_missing_counts_from_next_card(self):
        url = "https://www.facebook.com/reel/123"
        await self.load('''<main role="main">
            <div><video style="width:320px;height:500px"></video>
                <div role="button" aria-label="Like, 82 reactions">82</div></div>
            <div style="position:absolute;top:1000px"><video style="width:320px;height:500px"></video>
                <div role="button" aria-label="Like, 104 reactions">104</div>
                <div role="button" aria-label="Comment, 18 comments">18</div></div>
            </main>''', url)
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_engagement(self.page, url)
        self.assertIsNone(result["engagement"])
        self.assertIsNone(result["comments"])

    async def test_reel_carousel_rejects_two_visible_videos_and_wrong_permalink(self):
        url = "https://www.facebook.com/reel/123"
        for link, position in [("", ""), ('<a href="/reel/999">Other reel</a>', 'style="position:absolute;top:1000px"')]:
            await self.load(f'''<main role="main"><div>{link}<video style="width:100px;height:100px"></video>
                <div role="button" aria-label="Like, 82 reactions">82</div>
                <div role="button" aria-label="Comment, 5 comments">5</div></div>
                <div {position}><video style="width:100px;height:100px"></video></div></main>''', url)
            self.assertFalse((await self.page.evaluate(POST_ENGAGEMENT_JS, url))["matched"])
            await self.page.unroute("**/*")

    async def test_video_opens_comments_and_confirms_explicit_zero(self):
        url = "https://www.facebook.com/reel/1572038844747225/?rdid=tracking&share_url=ignored"
        await self.load('''<main role="main"><div>
            <video style="width:600px;height:320px"></video>
            <div role="button" aria-label="Like, 21 reactions">21</div>
            <div role="button" aria-label="Comment" aria-expanded="false"
                 onclick="this.setAttribute('aria-expanded','true'); document.querySelector('aside').hidden=false"></div>
            <div role="button" aria-label="Share, 15 shares">15</div></div>
            <div style="position:absolute;top:1000px"><video></video>
                <span>900 reactions</span><span>3 comments</span></div></main>
            <aside role="complementary" hidden><div>Caption: 99 comments</div>
                <h2>Comments</h2><span>No comments yet</span><div contenteditable="true">Write a comment…</div></aside>''', url)
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_engagement(self.page, url)
        self.assertEqual((result["reactions"], result["comments"], result["engagement"]), (21, 0, 21))
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(await self.page.get_by_role('button', name='Comment', exact=True).get_attribute('aria-expanded'), 'true')

    async def test_video_closed_comments_panel_does_not_confirm_zero(self):
        url = "https://www.facebook.com/reel/123"
        await self.load('''<main role="main"><video style="width:600px;height:320px"></video>
            <div role="button" aria-label="Like, 21 reactions">21</div>
            <div role="button" aria-label="Comment" aria-expanded="false"></div></main>
            <aside role="complementary"><h2>Comments</h2><span>No comments yet</span></aside>''', url)
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_engagement(self.page, url)
        self.assertIsNone(result["comments"])
        self.assertEqual(result["outcome"], "unavailable")

    async def test_video_missing_comment_count_is_not_assumed_zero(self):
        url = "https://www.facebook.com/reel/123"
        for panel in [
            '<aside role="complementary"><h2>Comments</h2><span>Loading…</span></aside>',
            '<aside role="complementary"><span>No comments yet</span><h2>Comments</h2></aside>',
            '<aside role="complementary" aria-label="Messenger"><h2>Comments</h2><span>No comments yet</span></aside>',
            '<aside role="complementary"><a href="/reel/999">Other</a><h2>Comments</h2><span>No comments yet</span></aside>',
            '<aside role="complementary"><h2>Comments</h2><span>No comments yet</span><article role="article" aria-label="Comment by Reader">Hello</article></aside>',
        ]:
            with self.subTest(panel=panel):
                await self.load('''<main role="main"><video style="width:600px;height:320px"></video>
                    <div role="button" aria-label="Like, 21 reactions">21</div>
                    <div role="button" aria-label="Comment" aria-expanded="true"></div></main>''' + panel, url)
                with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
                    result = await read_post_engagement(self.page, url)
                self.assertIsNone(result["comments"])
                self.assertIsNone(result["engagement"])
                await self.page.unroute("**/*")

    async def test_share_page_can_resolve_canonical_post_and_split_labels(self):
        url = "https://www.facebook.com/share/p/token"
        await self.load(f'<link rel="canonical" href="{URL}"><article role="article">'
                        f'<a href="{URL}">Post</a><div><span>All reactions:</span> <span>1.2K</span></div><span>4 comments</span></article>', url)
        result = await read_post_engagement(self.page, url)
        self.assertEqual(result["engagement"], 1204)

    async def test_shared_reel_canonical_video_slug_uses_numeric_identity(self):
        url = "https://www.facebook.com/share/r/token"
        await self.load('''<link rel="canonical" href="https://www.facebook.com/creator/videos/reel-title/123/">
            <article role="article"><a href="/reel/123">Reel</a>
            <div role="button" aria-label="Like, 82 reactions">82</div>
            <div role="button" aria-label="Comment, 5 comments">5</div></article>''', url)
        result = await read_post_engagement(self.page, url)
        self.assertEqual(result["engagement"], 87)

    async def test_missing_component_is_not_zero_but_explicit_zero_is_valid(self):
        await self.load(f'<article role="article"><a href="{URL}">Post</a><span>900 likes</span></article>')
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_engagement(self.page, URL)
        self.assertIsNone(result["engagement"])
        self.assertEqual(result["reactions"], 900)
        self.assertEqual(result["outcome"], "unavailable")
        self.assertIn("comments total", result["reason"])
        await self.page.unroute("**/*")
        await self.load(f'<article role="article"><a href="{URL}">Post</a><span>0 reactions</span><span>No comments yet</span></article>')
        result = await read_post_engagement(self.page, URL)
        self.assertEqual(result["engagement"], 0)
        self.assertEqual(result["outcome"], "ok")

    async def test_navigation_timeout_can_still_read_a_verified_loaded_post(self):
        await self.load(f'<article role="article"><a href="{URL}">Post</a><span>12 likes</span><span>3 comments</span></article>')
        with patch.object(self.page, "goto", new=AsyncMock(side_effect=PlaywrightTimeoutError("Still loading"))):
            result = await read_post_engagement(self.page, URL)
        self.assertEqual(result["engagement"], 15)
        await self.page.goto("https://www.facebook.com/other/posts/999")
        with patch.object(self.page, "goto", new=AsyncMock(side_effect=PlaywrightTimeoutError("Still loading"))), \
                patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_engagement(self.page, URL)
        self.assertIsNone(result["engagement"])
        self.assertEqual(result["reason_code"], "post_not_identified")

    async def test_photo_sidebar_is_read_and_comment_reactions_are_excluded(self):
        await self.load('''<main role="main"><img width="600" height="400" alt="Photo"></main>
            <aside role="complementary"><div role="button" aria-label="React with Like to this post"></div>
            <span>15 reactions</span><span>2 comments</span>
            <article role="article" aria-label="Comment by Someone"><span>900 likes</span></article></aside>
            <article role="article"><span>3M likes</span><span>5M comments</span></article>''', PHOTO)
        result = await read_post_engagement(self.page, PHOTO)
        self.assertEqual(result["engagement"], 17)

    async def test_compact_post_dialog_reads_action_totals_not_reaction_subtypes_or_background(self):
        await self.load(f'''<main role="main"><article role="article"><a href="/other/posts/999">Other post</a>
            <div role="button" aria-label="React with Like to Other's post">900</div>
            <div role="button" aria-label="Comment on Other's post, 30 comments">30</div></article></main>
            <div role="dialog"><h2>Someone's post</h2><a href="{URL}">Time</a>
            <a href="/photo?fbid=456"><img width="300" height="300"></a>
            <a href="/photo?fbid=789"><img width="300" height="300"></a><video></video><video></video>
            <div role="button" aria-label="React with Like to Someone's post">74</div>
            <div role="button" aria-label="Comment on Someone's post, 4 comments">4</div>
            <div role="button" aria-label="Like: 53 people"></div><div role="button" aria-label="Love: 21 people"></div>
            <article role="article" aria-label="Comment by a reader"><span>99 likes</span></article></div>''')
        result = await read_post_engagement(self.page, URL)
        self.assertEqual((result["reactions"], result["comments"], result["engagement"]), (74, 4, 78))

    async def test_modal_with_an_unrelated_post_permalink_is_not_used(self):
        await self.load('''<div role="dialog"><h2>Someone's post</h2><a href="/other/posts/999">Other post</a>
            <div role="button" aria-label="React with Like to Someone's post">74</div>
            <div role="button" aria-label="Comment on Someone's post, 4 comments">4</div></div>''')
        snapshot = await self.page.evaluate(POST_ENGAGEMENT_JS, URL)
        self.assertFalse(snapshot["matched"])

    async def test_photo_keeps_its_own_zero_counts_instead_of_following_parent_post(self):
        async def route(route):
            if "photo?" in route.request.url:
                body = (f'<main role="main"><img width="600" height="400"></main>'
                        f'<aside role="complementary"><div>This photo is from a post.</div><a href="{URL}">View Post</a>'
                        '<div role="button" aria-label="React with Like to this post"></div>'
                        '<span>0 reactions</span><span>No comments yet</span></aside>')
            else:
                body = f'<article role="article"><a href="{URL}">Post</a><span>125 reactions</span><span>10 comments</span></article>'
            await route.fulfill(content_type="text/html", body=body)
        await self.page.route("**/*", route)
        result = await read_post_engagement(self.page, PHOTO)
        self.assertEqual(result["engagement"], 0)
        self.assertEqual(result["resolved_url"], PHOTO)

    async def test_chat_counts_cannot_leak_through_an_unblocked_wrapper(self):
        await self.load(f'<main role="main"><article role="article"><a href="{URL}">Post</a>'
                        '<div><div data-pagelet="ChatTab"><span>900 likes</span>'
                        '<span>30 comments</span></div></div></article></main>')
        snapshot = await self.page.evaluate(POST_ENGAGEMENT_JS, URL)
        self.assertTrue(snapshot["matched"])
        counts = parse_engagement(snapshot["labels"])
        self.assertIsNone(counts["reactions"])
        self.assertIsNone(counts["comments"])
        self.assertIsNone(counts["engagement"])

    async def test_quoted_link_cannot_identify_another_posts_counts(self):
        await self.load(f'<main role="main"><article role="article">'
                        '<a href="/other/posts/999">Other post</a>'
                        f'<div data-ad-preview="message"><a href="{URL}">Quoted post</a></div>'
                        '<span>900 likes</span><span>30 comments</span></article></main>')
        snapshot = await self.page.evaluate(POST_ENGAGEMENT_JS, URL)
        self.assertFalse(snapshot["matched"])
        self.assertEqual(snapshot["labels"], [])

    async def test_photo_missing_counts_does_not_use_parent_counts(self):
        await self.load(f'<main role="main"><img width="600" height="400"></main>'
                        '<aside role="complementary"><div>This photo is from a post.</div>'
                        f'<a href="{URL}">View Post</a>'
                        '<div role="button" aria-label="React with Like to this post"></div></aside>', PHOTO)
        with patch.object(self.page, "wait_for_timeout", new=AsyncMock()):
            result = await read_post_engagement(self.page, PHOTO)
        self.assertIsNone(result["engagement"])
        self.assertEqual(result["outcome"], "unavailable")
        self.assertEqual(result["resolved_url"], PHOTO)

    async def test_photo_does_not_follow_unrelated_or_external_links(self):
        await self.load('''<main role="main"><img width="600" height="400"></main>
            <aside role="complementary"><div>This photo is from a post.</div><a href="https://example.com/posts/123">View Post</a>
            <div role="button" aria-label="React with Like to this post"></div><span>15 reactions</span><span>2 comments</span></aside>''', PHOTO)
        result = await read_post_engagement(self.page, PHOTO)
        self.assertEqual(result["engagement"], 17)
        self.assertEqual(result["resolved_url"], PHOTO)

    async def test_photo_login_overlay_and_redirect_require_reconnecting(self):
        await self.load('<main role="main"><img width="600" height="400"><input type="password">'
                        '<span>100 reactions</span><span>5 comments</span></main>', PHOTO)
        with self.assertRaisesRegex(FacebookLoginError, "Reconnect Facebook"):
            await read_post_engagement(self.page, PHOTO)
        page = AsyncMock()
        page.url = "https://www.facebook.com/checkpoint/123"
        with self.assertRaisesRegex(FacebookLoginError, "Reconnect Facebook"):
            await read_post_engagement(page, URL)


if __name__ == "__main__":
    unittest.main()
