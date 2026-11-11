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
from modules.velocity_comment_analysis import MAX_COMMENTS, bounded_text
from modules.facebook_content import CONTENT_GUARD_JS

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
            # Reel action buttons announce their action before the displayed total.
            text = re.sub(r"^(?:Like|Unlike|Remove Like),\s*(?=\d[\d.,]*\s?[KkMm]?\s+(?:reactions?|likes?)$)", "", text, flags=re.I)
            text = re.sub(r"^Comment,\s*(?=\d[\d.,]*\s?[KkMm]?\s+comments?$)", "", text, flags=re.I)
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
POST_ENGAGEMENT_JS = "requested => {" + CONTENT_GUARD_JS + r"""
    document.querySelectorAll('[data-sm-velocity-scope]').forEach(el => el.removeAttribute('data-sm-velocity-scope'));
    document.querySelectorAll('[data-sm-velocity-expand-comments]').forEach(el => el.removeAttribute('data-sm-velocity-expand-comments'));
    const identity = value => {
        try {
            const u = new URL(value, location.href);
            if (!/^(www\.|m\.|mbasic\.|web\.)?facebook\.com$/.test(u.hostname)) return '';
            if (u.searchParams.has('comment_id') || u.searchParams.has('reply_comment_id')) return '';
            if (/^\/(watch|video\.php)\/?$/.test(u.pathname)) return u.searchParams.get('v') || '';
            if (/^\/(story|permalink)\.php$/.test(u.pathname)) return u.searchParams.get('story_fbid') || '';
            if (/^\/photo(?:\.php)?\/?$/.test(u.pathname)) return u.searchParams.get('fbid') || '';
            // Video permalinks may include a title slug before the numeric ID.
            const video = u.pathname.match(/\/videos\/(?:[^/]+\/)?(\d+)\/?$/);
            if (video) return video[1];
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
    const messageSelector = '[data-ad-preview="message"], [data-ad-comet-preview="message"], [data-testid="post_message"]';
    const anchors = [...document.querySelectorAll('a[href]')].filter(a => visible(a) && !fbBlocked(a, true)
        && !a.closest(messageSelector));
    const photoPage = /^\/photo(?:\.php)?\/?$/.test(location.pathname)
        || location.pathname.includes('/photos/');
    const commentSelector = '[data-testid*="comment"], [data-ad-comet-preview="comment"], [aria-label^="Comment by"], [aria-label^="Reply by"]';
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
    const reelPage = /^\/reel\//.test(location.pathname);
    if (!roots.length && reelPage) {
        // Reels preload the next card below the viewport. Bind the current URL
        // to one substantially visible video, then stop before its next sibling.
        const activeVideos = [...document.querySelectorAll('[role="main"] video, [role="dialog"] video')]
            .filter(video => {
                if (!visible(video) || fbBlocked(video, true) || inComment(video)
                    || video.closest('[aria-hidden="true"], [inert]')) return false;
                const r = video.getBoundingClientRect();
                const width = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
                const height = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
                return r.width > 0 && r.height > 0 && width >= Math.min(r.width, innerWidth) / 2
                    && height >= Math.min(r.height, innerHeight) / 2;
            });
        if (activeVideos.length === 1) {
            for (let root = activeVideos[0].parentElement; root; root = root.parentElement) {
                if (fbBlocked(root, true) || inComment(root) || root.querySelectorAll('video').length !== 1) break;
                const ids = anchors.filter(a => root.contains(a) && !inComment(a)).map(a => identity(a.href)).filter(Boolean);
                if (ids.some(id => id !== finalId)) break;
                const action = [...root.querySelectorAll('[role="button"][aria-label], button[aria-label]')]
                    .some(el => visible(el) && !inComment(el) && !fbBlocked(el, true) && !el.closest(messageSelector)
                        && /^(?:(?:Like|Unlike|Remove Like), \d[\d.,]*(?:\s?[KM])? (?:reactions?|likes?)|Comment, \d[\d.,]*(?:\s?[KM])? comments?)$/i.test(el.getAttribute('aria-label')));
                if (action) { roots = [root]; break; }
                if (root.matches('[role="main"], [role="dialog"]')) break;
            }
        }
    }
    if (!roots.length) {
        // Media viewers may have no post permalink and use articles for comments.
        // Prefer the active dialog; a recommendation feed is never a fallback.
        const mediaRoots = [...document.querySelectorAll('[role="dialog"], [role="main"]')]
            .sort((a, b) => Number(b.getAttribute('role') === 'dialog') - Number(a.getAttribute('role') === 'dialog'));
        for (const root of mediaRoots) {
            if (!visible(root) || fbBlocked(root, true) || [...root.querySelectorAll('[role="article"]')]
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
        .filter(panel => visible(panel) && !fbBlocked(panel, true) && panel.querySelector(
            '[role="button"][aria-label^="React with"], [role="button"][aria-label^="Comment on"]')) : [];
    const panel = photoPanels.length === 1 ? photoPanels[0] : null;
    const containers = panel && !root.contains(panel) ? [root, panel] : [root];
    containers.forEach(el => el.setAttribute('data-sm-velocity-scope', 'true'));
    const excluded = '[data-ad-preview="message"], [data-ad-comet-preview="message"], '
        + '[data-testid="post_message"], [role="article"], ' + commentSelector;
    const blockedDescendants = containers.flatMap(container => [...container.querySelectorAll('*')])
        .filter(el => fbBlocked(el, true));
    for (const container of containers) for (const el of container.querySelectorAll('span, div, [role="button"], [aria-label], [title]')) {
        if (!visible(el) || fbBlocked(el, true) || inComment(el) || comments.some(comment => el.contains(comment))
            || blockedDescendants.some(blocked => el.contains(blocked))) continue;
        const parent = el.closest(excluded);
        if ((parent && parent !== container) || el.querySelector(excluded)) continue;
        const action = el.getAttribute('aria-label') || '';
        const number = (el.innerText || '').trim();
        if (reelPage && el.getAttribute('role') === 'button' && action === 'Comment'
            && el.getAttribute('aria-expanded') === 'false') el.setAttribute('data-sm-velocity-expand-comments', 'true');
        // Facebook's compact footer puts totals inside the reaction/comment buttons.
        if (el.getAttribute('role') === 'button' && /^React with .+ to .+post$/i.test(action)
            && /^\d[\d.,]*(?:\s?[KM])?$/i.test(number)) labels.push(number + ' reactions');
        const commentCount = action.match(/^Comment on .+post, (\d[\d.,]*(?:\s?[KM])?) comments?$/i);
        if (el.getAttribute('role') === 'button' && commentCount) labels.push(commentCount[1] + ' comments');
        for (const value of [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title')]) {
            if (value && value.length < 80) labels.push(value.trim());
        }
    }
    // Videos with no comments omit the total on the action button. Only accept
    // zero after opening this video's comments and seeing the explicit empty state.
    const commentsOpen = reelPage && [...root.querySelectorAll('[role="button"][aria-label="Comment"][aria-expanded="true"]')]
        .some(el => visible(el) && !inComment(el) && !fbBlocked(el, true) && !el.closest(messageSelector));
    if (commentsOpen) {
        const panels = [...document.querySelectorAll('[role="complementary"]')].filter(el =>
            visible(el) && !fbBlocked(el, true) && [...el.querySelectorAll('h2, [role="heading"]')]
                .some(heading => visible(heading) && heading.innerText.trim() === 'Comments'));
        if (panels.length === 1) {
            const panel = panels[0];
            const heading = [...panel.querySelectorAll('h2, [role="heading"]')]
                .find(el => visible(el) && el.innerText.trim() === 'Comments');
            const foreignPost = anchors.some(a => panel.contains(a) && !inComment(a)
                && identity(a.href) && identity(a.href) !== finalId);
            if (!foreignPost && !comments.some(comment => panel.contains(comment)) && [...panel.querySelectorAll('span, div')]
                .some(el => visible(el) && !fbBlocked(el, true) && !inComment(el) && !el.closest(messageSelector)
                    && (heading.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)
                    && el.innerText.trim() === 'No comments yet')) labels.push('No comments yet');
        }
    }
    return {matched: true, labels: [...new Set(labels)]};
}"""


# Read text only inside the post containers identified by POST_ENGAGEMENT_JS.
# Author names, timestamps, action buttons and neighboring posts are excluded.
POST_COMMENTS_JS = "limit => {" + CONTENT_GUARD_JS + r"""
    const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const roots = [...document.querySelectorAll('[data-sm-velocity-scope]')];
    const containers = [...new Set(roots.flatMap(root => [...root.querySelectorAll(
        '[aria-label^="Comment by"], [aria-label^="Reply by"], [data-testid="comment"], [role="article"]')]))]
        .filter(el => visible(el) && !fbBlocked(el, true) && (el.matches('[aria-label^="Comment by"], [aria-label^="Reply by"], [data-testid="comment"]')
            || [...el.querySelectorAll('a[href]')].some(a => a.closest('[role="article"]') === el
                && /[?&](?:comment_id|reply_comment_id)=/.test(a.href))));
    const result = [];
    for (const el of containers) {
        const owned = node => !containers.some(other => other !== el && el.contains(other) && other.contains(node));
        const explicit = [...el.querySelectorAll('[data-ad-comet-preview="comment"], [data-testid="comment_body"], [data-testid="comment-text"]')].filter(owned);
        const bodies = explicit.length ? explicit : [...el.querySelectorAll('[dir="auto"]')]
            .filter(node => owned(node) && !node.closest('a, button, [role="button"]')
                && !node.querySelector('[dir="auto"], h3, a[role="link"]'));
        const text = bodies.filter(visible).map(node => fbText(node, false, true)).filter(Boolean).join('\n').slice(0, 2000);
        if (!text) continue;
        const link = [...el.querySelectorAll('a[href]')].find(a => owned(a) && /[?&](?:comment_id|reply_comment_id)=/.test(a.href));
        const params = link ? new URL(link.href, location.href).searchParams : null;
        result.push({id: params?.get('reply_comment_id') || params?.get('comment_id') || '', text});
        if (result.length >= limit) break;
    }
    return result;
}"""


async def read_post_comments(page, url: str, total: int | None) -> dict:
    """Read at most 20 displayed comments, with three scoped expansion clicks."""
    comments, seen = [], set()
    for attempt in range(4):
        snapshot = await page.evaluate(POST_ENGAGEMENT_JS, url)
        if snapshot.get("login_required") or not snapshot.get("matched"):
            break
        for comment in await page.evaluate(POST_COMMENTS_JS, MAX_COMMENTS):
            text = bounded_text(comment.get("text", ""))
            ident = comment.get("id") or text
            if text and ident not in seen:
                comments.append({"id": comment.get("id", ""), "text": text})
                seen.add(ident)
            if len(comments) >= MAX_COMMENTS:
                break
        if len(comments) >= MAX_COMMENTS or attempt == 3:
            break
        more = page.locator('[data-sm-velocity-scope]').get_by_role("button", name=re.compile(
            r"^(?:View|See|Load) (?:(?:all|more|previous|earlier|\d+)\s+){1,3}comments(?:\s*\(\d+\))?$", re.I))
        if not await more.count() and attempt == 0 and not comments and total:
            # Some permalinks initially collapse their comments behind the count.
            more = page.locator('[data-sm-velocity-scope]').get_by_role("button", name=re.compile(
                rf"^{_NUMBER}\s+comments?$", re.I))
        if not await more.count():
            break
        try:
            await more.first.click(timeout=1500)
        except Exception:
            break  # Retain the sample already collected if an expansion fails.
        await page.wait_for_timeout(500)
    return {"comment_sample": comments, "comment_collection": {
        "status": "ok" if comments else "empty" if total == 0 else "unavailable", "limit": MAX_COMMENTS,
    }}


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
    comments_attempted = False
    counts = parse_engagement([])
    for _ in range(8):
        if re.search(r"facebook\.com/(?:login|checkpoint|two_step_verification)(?:[/?._]|$)", page.url):
            raise FacebookLoginError("Facebook is asking you to sign in. Reconnect Facebook in Scrape Data, then run again.")
        snapshot = await page.evaluate(POST_ENGAGEMENT_JS, active_url)
        if snapshot.get("login_required"):
            raise FacebookLoginError("Facebook is asking you to sign in. Reconnect Facebook in Scrape Data, then run again.")
        matched = matched or snapshot["matched"]
        counts = parse_engagement(snapshot["labels"])
        if counts["engagement"] is not None:
            return {**counts, "resolved_url": active_url,
                    "label": f"{counts['reactions']:,} likes/reactions + {counts['comments']:,} comments",
                    "outcome": "ok"}
        if _ == 7:
            break
        if snapshot["matched"] and counts["reactions"] is not None and counts["comments"] is None and not comments_attempted:
            toggle = page.locator('[data-sm-velocity-expand-comments]')
            if await toggle.count() == 1:
                comments_attempted = True
                try:
                    # Activate the identified control without scrolling the Reel
                    # carousel, which can change its active video during a click.
                    await asyncio.wait_for(toggle.evaluate("button => button.click()", timeout=1500), timeout=2)
                except Exception:
                    pass  # An unopened or unloaded panel must never imply zero.
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
                # Keep successfully read totals even if comment collection fails.
                try:
                    async with asyncio.timeout(10):
                        result.update(await read_post_comments(page, result.get("resolved_url", post["url"]), result.get("comments")))
                except Exception as exc:
                    log.warning("Velocity comments could not be read (%s)", type(exc).__name__)
                    result.update(comment_sample=[], comment_collection={"status": "error", "limit": MAX_COMMENTS})
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
