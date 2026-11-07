"""Read displayed reactions and comments for individual Facebook posts."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import logging
import re

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from modules.engagement import parse_count
from modules.facebook_remote import remote_facebook_context
from modules.facebook_session import (
    FacebookLoginError, _profile_session, facebook_context_options,
    facebook_storage_path, launch_facebook_browser,
)
from modules.velocity_tracking import normalize_post_url

log = logging.getLogger(__name__)
_NUMBER = r"\d[\d.,]*(?:\s?[KkMm])?"
_REACTIONS = re.compile(rf"(?:({_NUMBER})\s+reactions?|(?:all\s+)?reactions?\s*:?\s*({_NUMBER}))", re.I)
_LIKES = re.compile(rf"(?:({_NUMBER})\s+likes?|likes?\s*:?\s*({_NUMBER}))", re.I)
_COMMENTS = re.compile(rf"(?:(?:view\s+all\s+)?({_NUMBER})\s+comments?|comments?\s*:?\s*({_NUMBER}))", re.I)


def parse_engagement(labels: list[str]) -> dict:
    """Read explicit totals, without adding reaction subtypes or comment likes."""
    found = {"reactions": set(), "likes": set(), "comments": set()}
    for label in labels:
        for part in [label, *re.split(r"[·•|\n\r]", label)]:
            text = " ".join(part.split())
            for key, pattern in (("reactions", _REACTIONS), ("likes", _LIKES), ("comments", _COMMENTS)):
                match = pattern.fullmatch(text)
                if match:
                    count = parse_count(match.group(1) or match.group(2))
                    if count is not None:
                        found[key].add(count)
            if text.lower() in {"no reactions", "no reactions yet", "be the first to react"}:
                found["reactions"].add(0)
            if text.lower() in {"no comments", "no comments yet", "be the first to comment.", "be the first to comment"}:
                found["comments"].add(0)
    reactions = found["reactions"] or found["likes"]
    counts = {"reactions": next(iter(reactions)) if len(reactions) == 1 else None,
              "comments": next(iter(found["comments"])) if len(found["comments"]) == 1 else None}
    counts["engagement"] = (counts["reactions"] + counts["comments"]
                            if all(value is not None for value in counts.values()) else None)
    return counts


# Only inspect the requested post's container. Exclude nested comments, post
# message text and other articles; never take counts from another post.
POST_ENGAGEMENT_JS = r"""requested => {
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
    if ([...document.querySelectorAll('input[type="password"]')].some(visible)) {
        return {matched: false, labels: [], login_required: true};
    }
    const sharePage = /^\/share\/[pvr]\//.test(location.pathname)
        && new URL(requested).pathname.replace(/\/$/, '') === location.pathname.replace(/\/$/, '');
    const canonical = document.querySelector('link[rel="canonical"]')?.href
        || document.querySelector('meta[property="og:url"]')?.content || '';
    const finalId = identity(location.href) || (sharePage ? identity(canonical) : '');
    const requestedId = identity(requested);
    // Share URLs must resolve to a specific post before we can attribute counts.
    if (!finalId || (requestedId && requestedId !== finalId)) return {matched: false, labels: []};
    const anchors = [...document.querySelectorAll('a[href]')].filter(a => visible(a));
    const photoPage = /^\/photo(?:\.php)?\/?$/.test(location.pathname)
        || location.pathname.includes('/photos/');
    const commentSelector = '[data-testid*="comment"], [aria-label^="Comment by"], [aria-label^="Reply by"]';
    const comments = [...document.querySelectorAll('[role="article"]')].filter(article =>
        article.matches(commentSelector) || anchors.some(a => article.contains(a)
            && a.closest('[role="article"]') === article
            && /[?&](?:comment_id|reply_comment_id)=/.test(a.href)));
    const inComment = el => !!el.closest(commentSelector) || comments.some(comment => comment.contains(el));
    const candidates = [];
    for (const anchor of anchors.filter(a => !inComment(a) && identity(a.href) === finalId)) {
        const root = anchor.closest('[role="article"], [data-pagelet^="FeedUnit"]');
        if (root && visible(root) && !candidates.includes(root)) candidates.push(root);
    }
    let roots = candidates.filter(root => !candidates.some(other => other !== root && root.contains(other)));
    if (!roots.length) {
        // Media viewers may have no post permalink and use articles for comments.
        // Prefer the active dialog; a recommendation feed is never a fallback.
        const mediaRoots = [...document.querySelectorAll('[role="dialog"], [role="main"]')]
            .sort((a, b) => Number(b.getAttribute('role') === 'dialog') - Number(a.getAttribute('role') === 'dialog'));
        for (const root of mediaRoots) {
            if (!visible(root) || [...root.querySelectorAll('[role="article"]')]
                .some(article => visible(article) && !inComment(article))) continue;
            const ids = new Set(anchors.filter(a => root.contains(a) && !inComment(a))
                .map(a => identity(a.href)).filter(Boolean));
            const postIds = new Set(anchors.filter(a => root.contains(a) && !inComment(a)
                && /\/(?:posts|permalink)\/|\/(?:story|permalink)\.php/.test(new URL(a.href).pathname))
                .map(a => identity(a.href)).filter(Boolean));
            const photos = [...root.querySelectorAll('img')].filter(img => {
                const rect = img.getBoundingClientRect();
                return visible(img) && !inComment(img) && rect.width >= 200 && rect.height >= 200;
            });
            if ((ids.size === 1 && ids.has(finalId)) ||
                (root.getAttribute('role') === 'dialog' && postIds.size === 1 && postIds.has(finalId)) ||
                (ids.size === 0 && (root.querySelectorAll('video').length === 1
                    || (photoPage && photos.length === 1)))) { roots = [root]; break; }
        }
    }
    if (roots.length !== 1) return {matched: false, labels: []};
    const root = roots[0], labels = [];
    // Facebook places a photo's engagement in a sibling sidebar, outside main.
    const photoPanels = photoPage ? [...document.querySelectorAll('[role="complementary"]')]
        .filter(panel => visible(panel) && panel.querySelector(
            '[role="button"][aria-label^="React with"], [role="button"][aria-label^="Comment on"]')) : [];
    const panel = photoPanels.length === 1 ? photoPanels[0] : null;
    const parentLink = panel && panel.innerText.trim().startsWith('This photo is from a post.')
        ? anchors.find(a => panel.contains(a) && a.innerText.trim() === 'View Post'
            && identity(a.href) && identity(a.href) !== finalId) : null;
    const containers = panel && !root.contains(panel) ? [root, panel] : [root];
    const excluded = '[data-ad-preview="message"], [data-ad-comet-preview="message"], '
        + '[data-testid="post_message"], [data-testid*="comment"], [role="article"]';
    for (const container of containers) for (const el of container.querySelectorAll('span, div, [role="button"], [aria-label], [title]')) {
        if (!visible(el) || inComment(el) || comments.some(comment => el.contains(comment))) continue;
        const parent = el.closest(excluded);
        if ((parent && parent !== container) || el.querySelector(excluded)) continue;
        const action = el.getAttribute('aria-label') || '';
        const number = (el.innerText || '').trim();
        // Facebook's compact footer puts totals inside the reaction/comment buttons.
        if (el.getAttribute('role') === 'button' && /^React with .+ to .+post$/i.test(action)
            && /^\d[\d.,]*(?:\s?[KM])?$/i.test(number)) labels.push(number + ' reactions');
        const commentCount = action.match(/^Comment on .+post, (\d[\d.,]*(?:\s?[KM])?) comments?$/i);
        if (el.getAttribute('role') === 'button' && commentCount) labels.push(commentCount[1] + ' comments');
        for (const value of [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title')]) {
            if (value && value.length < 80) labels.push(value.trim());
        }
    }
    return {matched: true, labels: [...new Set(labels)], ...(parentLink ? {parent_url: parentLink.href} : {})};
}"""


async def _navigate(page, url: str, timeout: int) -> None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except PlaywrightTimeoutError:
        # Facebook can display the post while navigation is still pending.
        # The DOM extractor still requires the requested identity and container.
        pass


async def read_post_engagement(page, url: str) -> dict:
    active_url = normalize_post_url(url)
    await _navigate(page, active_url, 30_000)
    matched = False
    counts = parse_engagement([])
    followed_parent, attempts_on_page = False, 0
    for _ in range(16):
        if re.search(r"facebook\.com/(?:login|checkpoint|two_step_verification)(?:[/?._]|$)", page.url):
            raise FacebookLoginError("Facebook is asking you to sign in. Reconnect Facebook in Scrape Data, then run again.")
        snapshot = await page.evaluate(POST_ENGAGEMENT_JS, active_url)
        if snapshot.get("login_required"):
            raise FacebookLoginError("Facebook is asking you to sign in. Reconnect Facebook in Scrape Data, then run again.")
        if snapshot.get("parent_url") and not followed_parent:
            active_url = normalize_post_url(snapshot["parent_url"])
            await _navigate(page, active_url, 15_000)
            followed_parent, attempts_on_page, matched = True, 0, False
            counts = parse_engagement([])
            continue
        matched = matched or snapshot["matched"]
        counts = parse_engagement(snapshot["labels"])
        if counts["engagement"] is not None:
            return {**counts, "resolved_url": active_url,
                    "label": f"{counts['reactions']:,} likes/reactions + {counts['comments']:,} comments",
                    "outcome": "ok"}
        attempts_on_page += 1
        if attempts_on_page >= 8:
            break
        await page.wait_for_timeout(1000)
    if not matched:
        return {**counts, "resolved_url": active_url, "outcome": "error", "reason_code": "post_not_identified",
                "reason": "The requested post could not be identified on the page. Open the link on Facebook "
                          "to check that it loads, then run the card again."}
    missing = " and ".join(name for key, name in (("reactions", "likes/reactions"), ("comments", "comments"))
                           if counts[key] is None)
    return {**counts, "resolved_url": active_url, "outcome": "unavailable", "reason_code": "engagement_not_shown",
            "reason": f"The {missing} total could not be read on this post. "
                      "Open the post on Facebook to check its counts, then run again. Both totals are needed."}


async def collect_engagement(email: str, posts: list[dict], *, hosted: bool, progress, partial) -> list[dict]:
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
                    result = await read_post_engagement(page, post["url"])
            except FacebookLoginError:
                raise
            except Exception as exc:
                log.warning("Velocity post check failed (%s)", type(exc).__name__)
                result = {**parse_engagement([]), "outcome": "error",
                          "reason": "This post could not be checked. Run again to retry."}
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
