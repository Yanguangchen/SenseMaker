"""Facebook extraction never treats Messenger, page chrome, or replies as posts."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import async_playwright

from modules.scraper import _harvest_visible_posts, _collect_page_level_fallback, _click_expand_controls
from modules.velocity_scraper import read_post_comments, read_post_engagement

URL = "https://www.facebook.com/example/posts/123"


def post(text, ident=123):
    return (f'<article role="article"><a href="/example/posts/{ident}">Author · 1h</a>'
            f'<div data-ad-preview="message">{text}</div><button>Like</button><button>Share</button></article>')


class FacebookContentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def load(self, html):
        await self.page.route("**/*", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html))
        await self.page.goto(URL)

    async def harvest(self):
        persist = AsyncMock()
        rows = await _harvest_visible_posts(self.page, URL, set(), persist)
        self.assertEqual(persist.await_count, len(rows))
        return rows

    async def test_post_body_and_comment_text_exclude_all_chrome(self):
        text = "This public post discusses Facebook Messenger and customer support."
        await self.load('<nav role="navigation">Contacts Notifications Friends</nav><main role="main">'
                        f'<article role="article"><a href="{URL}">Author · 1h</a>'
                        f'<div data-ad-preview="message">{text}<span hidden>Hidden noise</span><button>See more</button></div>'
                        '<span>All reactions: 10</span><button>Like</button><button>Share</button>'
                        '<article role="article" aria-label="Comment by Person"><a href="/person">Person</a>'
                        '<div dir="auto">Helpful public reply</div><button>Reply</button>'
                        f'<a href="{URL}?comment_id=1">1h</a></article></article></main>')
        rows = await self.harvest()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["raw_text"], text)
        self.assertEqual(rows[0]["comments"], ["Helpful public reply"])
        self.assertEqual(rows[0]["url"], URL)

    async def test_messenger_sidebar_chat_dialog_and_forwarded_posts_are_excluded(self):
        await self.load('<main role="main">' + post('A legitimate public post about delivery service.')
                        + '<aside role="complementary">' + post('Private sidebar message with a forwarded link.', 2) + '</aside>'
                        + '<div data-pagelet="ChatTab"><div role="feed">' + post('Private Messenger chat must never be collected.', 3) + '</div></div>'
                        + '<div role="dialog" aria-label="Chats">' + post('Private conversation with a forwarded post.', 4) + '</div></main>')
        rows = await self.harvest()
        self.assertEqual([row["url"] for row in rows], [URL])
        self.assertNotIn('Private', str(rows))

    async def test_whole_container_text_group_links_and_link_only_results_are_rejected(self):
        await self.load('<main role="main"><article role="article">'
                        f'<a href="{URL}">Post</a>Very long navigation and unrelated panel content without a post body.</article>'
                        '<article role="article"><a href="/groups/example">Group home</a>'
                        '<div data-ad-preview="message">Not enough evidence to identify a Facebook post.</div></article>'
                        '<article role="article"><div data-ad-preview="message">A forwarded link in a message is not a post header: '
                        f'<a href="{URL}">Forwarded post</a></div></article></main>')
        self.assertEqual(await self.harvest(), [])

    async def test_comment_containers_and_outside_feed_articles_cannot_become_posts(self):
        await self.load('<main role="main"><article role="article" aria-label="Comment by Someone">'
                        f'<a href="{URL}?comment_id=3">Comment permalink</a>'
                        '<div data-ad-preview="message">This comment must not become a standalone post.</div></article></main>'
                        + post('An unrelated floating article outside the feed.', 4))
        self.assertEqual(await self.harvest(), [])

    async def test_post_dialog_is_allowed_and_mixed_post_identity_is_rejected(self):
        await self.load('<div role="dialog" aria-label="Post">' + post('A public post opened inside its own Facebook dialog.')
                        + '</div><main role="main"><article role="article"><a href="/example/posts/2">One</a>'
                        '<a href="/example/posts/3">Two</a><div data-ad-preview="message">Ambiguous content must be skipped safely.</div></article></main>')
        self.assertEqual([row["url"] for row in await self.harvest()], [URL])

    async def test_nested_feedunit_does_not_hide_post_header(self):
        text = "A public post whose message is inside a nested Facebook wrapper."
        await self.load('<main role="main"><article role="article">'
                        f'<a href="{URL}">1h</a><div data-pagelet="FeedUnit_0">'
                        f'<div data-ad-comet-preview="message">{text}</div></div>'
                        '<article role="article" aria-label="Comment by Person">'
                        '<div dir="auto">Public reply</div></article></article></main>')
        rows = await self.harvest()
        self.assertEqual([row["raw_text"] for row in rows], [text])
        self.assertEqual(rows[0]["comments"], ["Public reply"])

    async def test_unmarked_post_wrappers_and_all_message_selectors(self):
        selectors = ['data-ad-preview', 'data-ad-comet-preview', 'data-testid']
        html = '<main role="main"><div role="feed">'
        for index, attribute in enumerate(selectors):
            value = 'post_message' if attribute == 'data-testid' else 'message'
            html += (f'<div><div><a href="/example/posts/{index}">1h</a></div>'
                     f'<section><span {attribute}="{value}">Post {index}</span></section></div>')
        await self.load(html + '</div></main>')
        rows = await self.harvest()
        self.assertEqual([row["raw_text"] for row in rows], ['Post 0', 'Post 1', 'Post 2'])
        self.assertEqual(len({row["url"] for row in rows}), 3)

    async def test_message_cannot_borrow_a_neighboring_posts_permalink(self):
        await self.load('<main role="main"><div><div>'
                        '<div data-ad-preview="message">Body without its own post identity.</div></div>'
                        + post('A neighboring identifiable public post.') + '</div></main>')
        rows = await self.harvest()
        self.assertEqual([row["raw_text"] for row in rows], ['A neighboring identifiable public post.'])

    async def test_unmarked_chat_messages_are_still_excluded(self):
        message = (f'<div><a href="{URL}">Forwarded post</a>'
                   '<span data-ad-comet-preview="message">Private forwarded message</span></div>')
        await self.load('<main role="main"><div data-pagelet="Messenger">' + message
                        + '</div><aside role="complementary">' + message + '</aside>'
                        '<nav role="navigation">' + message + '</nav></main>')
        self.assertEqual(await self.harvest(), [])

    async def test_nested_message_markers_are_deduplicated(self):
        await self.load('<main role="main"><div>'
                        f'<a href="{URL}">1h</a><div data-ad-preview="message">'
                        '<span data-ad-comet-preview="message">Short post!</span></div></div></main>')
        rows = await self.harvest()
        self.assertEqual([row["raw_text"] for row in rows], ['Short post!'])

    async def test_expansion_never_clicks_chat_or_navigation_controls(self):
        await self.load('<main role="main">' + post('A real public post with expandable content. '
                        '<button onclick="document.body.dataset.postExpanded=1">See more</button>')
                        + '<div data-pagelet="Messenger"><div role="feed">'
                        + post('Private chat containing an expandable forwarded message. '
                               '<button onclick="document.body.dataset.chatExpanded=1">See more</button>', 2)
                        + '</div></div></main>')
        await _click_expand_controls(self.page)
        self.assertEqual(await self.page.evaluate('document.body.dataset.postExpanded'), '1')
        self.assertIsNone(await self.page.evaluate('document.body.dataset.chatExpanded'))

    async def test_fallback_never_reads_the_page_or_title(self):
        page = MagicMock()
        result = await _collect_page_level_fallback(URL, page)
        page.locator.assert_not_called()
        page.title.assert_not_called()
        self.assertEqual(result["source_type"], "batch_warning")

    async def test_velocity_comments_also_exclude_chat_content(self):
        await self.load(f'<main role="main"><article role="article"><a href="{URL}">Post</a>'
                        '<span>10 likes</span><span>1 comment</span>'
                        '<article role="article" aria-label="Comment by Person"><div dir="auto">Public feedback</div>'
                        f'<a href="{URL}?comment_id=1">1h</a></article>'
                        '<div data-pagelet="ChatTab"><article role="article" aria-label="Comment by Private">'
                        f'<div dir="auto">Private chat message</div><a href="{URL}?comment_id=2">2h</a></article></div>'
                        '</article></main>')
        result = await read_post_engagement(self.page, URL)
        self.assertEqual(result["engagement"], 11)
        comments = await read_post_comments(self.page, URL, 1)
        self.assertEqual(comments["comment_sample"], [{"id": "1", "text": "Public feedback"}])
