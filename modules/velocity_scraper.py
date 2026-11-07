"""Read displayed views for specific Facebook posts without collecting a feed."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import logging
import re

from playwright.async_api import async_playwright

from modules.engagement import parse_count
from modules.facebook_remote import remote_facebook_context
from modules.facebook_session import (
    FacebookLoginError, _profile_session, facebook_context_options,
    facebook_storage_path, launch_facebook_browser,
)
from modules.velocity_tracking import normalize_post_url

log = logging.getLogger(__name__)
_NUMBER = r"\d[\d.,]*(?:\s?[KkMm])?"
_VIEWS = re.compile(rf"(?:({_NUMBER})\s+(?:video\s+)?views?|views?\s*:?\s*({_NUMBER}))", re.I)


def parse_views(labels: list[str]) -> tuple[int | None, str]:
    """Accept only explicit view labels; ambiguous or absent counts are unknown."""
    found = {}
    for label in labels:
        text = " ".join(label.split())
        match = _VIEWS.fullmatch(text)
        if match:
            count = parse_count(match.group(1) or match.group(2))
            if count is not None:
                found[count] = text
    return next(iter(found.items())) if len(found) == 1 else (None, "")


# Only inspect the requested post's container. Exclude nested comments, post
# message text and other articles; never take the first view count on the page.
VIEW_LABELS_JS = r"""requested => {
    const identity = value => {
        try {
            const u = new URL(value, location.href);
            if (!/^(www\.|m\.|mbasic\.|web\.)?facebook\.com$/.test(u.hostname)) return '';
            if (u.searchParams.has('comment_id') || u.searchParams.has('reply_comment_id')) return '';
            if (/^\/(watch|video\.php)\/?$/.test(u.pathname)) return u.searchParams.get('v') || '';
            if (/^\/(story|permalink)\.php$/.test(u.pathname)) return u.searchParams.get('story_fbid') || '';
            if (/^\/photo(?:\.php)?\/?$/.test(u.pathname)) return u.searchParams.get('fbid') || '';
            const match = u.pathname.match(/\/(?:posts|permalink|videos|reel)\/([^/]+)/);
            if (match) return match[1];
            if (u.pathname.includes('/photos/')) return u.pathname.replace(/\/$/, '').split('/').pop();
        } catch (_) {}
        return '';
    };
    const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const sharePage = /^\/share\/[pvr]\//.test(location.pathname)
        && new URL(requested).pathname.replace(/\/$/, '') === location.pathname.replace(/\/$/, '');
    const canonical = document.querySelector('link[rel="canonical"]')?.href
        || document.querySelector('meta[property="og:url"]')?.content || '';
    const finalId = identity(location.href) || (sharePage ? identity(canonical) : '');
    const requestedId = identity(requested);
    // Share URLs must resolve to a specific post before we can attribute counts.
    if (!finalId || (requestedId && requestedId !== finalId)) return {matched: false, labels: []};
    const anchors = [...document.querySelectorAll('a[href]')].filter(a => visible(a));
    const candidates = [];
    for (const anchor of anchors.filter(a => identity(a.href) === finalId)) {
        const root = anchor.closest('[role="article"], [data-pagelet^="FeedUnit"]');
        if (root && visible(root) && !candidates.includes(root)) candidates.push(root);
    }
    let roots = candidates.filter(root => !candidates.some(other => other !== root && root.contains(other)));
    if (!roots.length) {
        // Video/reel layouts may not use articles. Accept one unambiguous post
        // container only; pages containing a recommendation feed stay unknown.
        for (const root of document.querySelectorAll('[role="dialog"], [role="main"]')) {
            if (!visible(root) || root.querySelector('[role="article"]')) continue;
            const ids = new Set(anchors.filter(a => root.contains(a)).map(a => identity(a.href)).filter(Boolean));
            if ((ids.size === 1 && ids.has(finalId)) ||
                (ids.size === 0 && root.querySelectorAll('video').length === 1)) { roots = [root]; break; }
        }
    }
    if (roots.length !== 1) return {matched: false, labels: []};
    const root = roots[0], labels = [];
    const excluded = '[data-ad-preview="message"], [data-ad-comet-preview="message"], '
        + '[data-testid="post_message"], [data-testid*="comment"], [role="article"]';
    for (const el of root.querySelectorAll('span, div, [role="button"], [aria-label], [title]')) {
        if (!visible(el)) continue;
        const parent = el.closest(excluded);
        if ((parent && parent !== root) || el.querySelector(excluded)) continue;
        for (const value of [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title')]) {
            if (value && value.length < 80) labels.push(value.trim());
        }
    }
    return {matched: true, labels: [...new Set(labels)]};
}"""


async def read_post_views(page, url: str) -> dict:
    await page.goto(normalize_post_url(url), wait_until="domcontentloaded", timeout=40_000)
    matched = False
    for attempt in range(8):
        if re.search(r"facebook\.com/(?:login|checkpoint|two_step_verification)(?:[/?._]|$)", page.url):
            raise FacebookLoginError("Facebook is asking you to sign in. Reconnect Facebook in Scrape Data, then run again.")
        snapshot = await page.evaluate(VIEW_LABELS_JS, url)
        matched = matched or snapshot["matched"]
        count, label = parse_views(snapshot["labels"])
        if count is not None:
            return {"views": count, "label": label, "outcome": "ok"}
        if attempt < 7:
            await page.wait_for_timeout(1000)
    return {"views": None, "outcome": "unavailable", "reason": (
        "Facebook did not display a view count for this post." if matched else
        "The requested post could not be identified on the page.")}


async def collect_views(email: str, posts: list[dict], *, hosted: bool, progress, partial) -> list[dict]:
    """Reuse one private authenticated browser for all rows; bound every visit."""
    results = []
    async with async_playwright() as playwright, AsyncExitStack() as resources:
        if hosted:
            context = await resources.enter_async_context(remote_facebook_context(
                playwright, email, timeout_seconds=660, check_before_use=True, verify_on_exit=False))
        else:
            browser = await launch_facebook_browser(playwright, headless=True)
            resources.push_async_callback(browser.close)
            context = await browser.new_context(**facebook_context_options(facebook_storage_path()))
            resources.push_async_callback(context.close)
            page = await context.new_page()
            if (await _profile_session(page, context)).status != "connected":
                raise FacebookLoginError("Connect Facebook in Scrape Data before running a velocity check.")
            await page.close()
        for index, post in enumerate(posts):
            progress(index, f"Checking post {index + 1} of {len(posts)}")
            page = await context.new_page()
            try:
                async with asyncio.timeout(55):
                    result = await read_post_views(page, post["url"])
            except FacebookLoginError:
                raise
            except Exception as exc:
                log.warning("Velocity post check failed (%s)", type(exc).__name__)
                result = {"views": None, "outcome": "error", "reason": "This post could not be checked. Run again to retry."}
            finally:
                # Closing a failed tab must not discard previously read counts.
                try:
                    await page.close()
                except Exception:
                    pass
            results.append({"post_id": post["id"], "url": post["url"],
                            "at": datetime.now(timezone.utc).isoformat(), **result})
            partial(list(results))
            progress(index + 1, f"Checked {index + 1} of {len(posts)} posts")
    return results
