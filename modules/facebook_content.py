"""DOM boundaries shared by Facebook feed and velocity collectors."""

POST_MESSAGE_SELECTOR = '[data-ad-preview="message"], [data-ad-comet-preview="message"], [data-testid="post_message"]'

# Inspect structural containers, never words in a post: a real post discussing
# Messenger must still be collected, while a private chat must never be read.
CONTENT_GUARD_JS = r"""
const fbBlocked = (node, allowSidebar = false) => {
    if (!node || (!allowSidebar && node.closest('[role="complementary"]'))
        || node.closest('[role="navigation"], [role="banner"], '
        + '[data-pagelet*="ChatTab"], [data-pagelet*="Messenger"], [data-pagelet*="Messaging"], '
        + '[data-testid*="mwchat"], [data-testid*="messenger"], [data-testid*="chat-tab"], '
        + '[data-scope="messages"], [contenteditable="true"], [role="textbox"]')) return true;
    for (let el = node; el; el = el.parentElement) {
        if (['dialog', 'region', 'complementary', 'grid'].includes(el.getAttribute('role'))
            && /messenger|\bchats?\b|\bconversations?\b|\bmessages?\b/i.test(el.getAttribute('aria-label') || '')) return true;
    }
    return false;
};
const fbVisible = node => !!node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
const fbComment = node => !!node.closest('[aria-label^="Comment by"], [aria-label^="Reply by"], '
    + '[data-testid="comment"], [data-testid="comment_body"], [data-ad-comet-preview="comment"]');
const fbText = (node, excludeComments = false, allowSidebar = false) => {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT), parts = [];
    let text;
    while ((text = walker.nextNode())) {
        const parent = text.parentElement;
        if (fbBlocked(parent, allowSidebar) || !fbVisible(parent)
            || (excludeComments && (fbComment(parent) || parent.closest('[role="article"]') !== node.closest('[role="article"]')))
            || parent.closest('button, [role="button"], '
            + 'input, textarea, script, style, [aria-hidden="true"]')) continue;
        const value = text.nodeValue.trim();
        if (value) parts.push(value);
    }
    return parts.join(' ').trim();
};
"""

FACEBOOK_POST_JS = "(node) => {" + CONTENT_GUARD_JS + r"""
    if (fbBlocked(node) || !fbVisible(node) || fbComment(node)
        || /^\/(?:messages|messenger|notifications|friends|marketplace)(?:\/|$)/.test(location.pathname)) return null;
    const messageSelector = '[data-ad-preview="message"], [data-ad-comet-preview="message"], [data-testid="post_message"]';
    const regionSelector = '[role="main"], [role="feed"], [role="dialog"], [data-pagelet="MainFeed"]';
    const message = node.closest(messageSelector) || node.querySelector(messageSelector);
    if (!message || fbBlocked(message) || fbComment(message) || !fbVisible(message)
        || !message.closest(regionSelector)) return null;
    // Start with an explicit message body, then find its header. Facebook can
    // put the body in a nested FeedUnit or omit article/FeedUnit roles entirely.
    // Never cross a feed boundary or another independent message body.
    let body = message;
    while (body.parentElement && body.parentElement.closest(messageSelector)) {
        body = body.parentElement.closest(messageSelector);
    }
    const rawText = fbText(body, true).slice(0, 16000);
    if (!rawText) return null;
    const postURL = href => {
        try {
            const url = new URL(href, location.href);
            if (!/^(www\.|m\.|mbasic\.|web\.)?facebook\.com$/.test(url.hostname)) return '';
            if (url.searchParams.has('comment_id') || url.searchParams.has('reply_comment_id')) return '';
            const path = url.pathname.replace(/\/$/, '');
            const direct = /^\/(?:[^/]+\/posts\/[^/]+|groups\/[^/]+\/(?:posts|permalink)\/[^/]+|(?:[^/]+\/)?videos\/[^/]+|reel\/[^/]+|[^/]+\/photos\/(?:[^/]+\/)*[^/]+)$/.test(path);
            const query = (['/story.php', '/permalink.php'].includes(path) && url.searchParams.has('story_fbid'))
                || (['/photo', '/photo.php'].includes(path) && url.searchParams.has('fbid'))
                || (['/watch', '/video.php'].includes(path) && url.searchParams.has('v'));
            if (!direct && !query) return '';
            for (const key of [...url.searchParams.keys()]) if (!['story_fbid', 'fbid', 'id', 'v'].includes(key)) url.searchParams.delete(key);
            url.hash = '';
            return url.href;
        } catch (_) { return ''; }
    };
    let root = null, permalinks = [];
    for (let candidate = body.parentElement; candidate; candidate = candidate.parentElement) {
        if (candidate.matches(regionSelector) || fbBlocked(candidate) || fbComment(candidate)) break;
        const foreignBodies = [...candidate.querySelectorAll(messageSelector)].filter(el =>
            el !== body && !body.contains(el) && !el.contains(body)
            && !fbBlocked(el) && !fbComment(el) && fbVisible(el));
        if (foreignBodies.length) break;
        const links = [...candidate.querySelectorAll('a[href]')].filter(a =>
            !fbBlocked(a) && !fbComment(a) && fbVisible(a) && !body.contains(a)
            && !a.closest('[role="article"][aria-label^="Comment"], [role="article"][aria-label^="Reply"]'));
        const urls = [...new Set(links.map(a => postURL(a.href)).filter(Boolean))];
        if (urls.length > 1) return null;
        if (urls.length === 1) {
            root = candidate;
            permalinks = urls;
        }
        // Stop at the post's article. Within it, include comments and detect
        // conflicting identities even if a smaller wrapper already matched.
        if (candidate.matches('[role="article"]')) break;
    }
    if (!root) return null;
    const comments = [], seen = new Set();
    const commentRoots = [...root.querySelectorAll('[aria-label^="Comment by"], [aria-label^="Reply by"], '
        + '[data-testid="comment"], [role="article"]')].filter(el => !fbBlocked(el) && fbVisible(el)
        && (fbComment(el) || [...el.querySelectorAll('a[href]')].some(a => a.closest('[role="article"]') === el
            && /[?&](?:comment_id|reply_comment_id)=/.test(a.href))));
    for (const comment of commentRoots) {
        const ownComment = el => !commentRoots.some(other => other !== comment && comment.contains(other) && other.contains(el));
        const explicit = [...comment.querySelectorAll('[data-ad-comet-preview="comment"], [data-testid="comment_body"], [data-testid="comment-text"]')].filter(ownComment);
        const nodes = explicit.length ? explicit : [...comment.querySelectorAll('[dir="auto"]')].filter(el => ownComment(el)
            && !el.closest('a, button, [role="button"], h3, h4') && !el.querySelector('[dir="auto"], h3, h4, a[role="link"]'));
        const text = nodes.filter(el => !fbBlocked(el) && fbVisible(el)).map(node => fbText(node)).filter(Boolean).join(' ').slice(0, 2000);
        if (text && !seen.has(text)) { seen.add(text); comments.push(text); }
        if (comments.length >= 12) break;
    }
    return {url: permalinks[0], raw_text: rawText, comments};
}"""
