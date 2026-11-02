"""Recent public posts from X through the official read API.

Collection uses a server bearer token and recent search (about the last 7 days).
There is no browser fallback. Usernames, profiles and view counts are not stored.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi

from modules.config import get_setting

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
TWEET_FIELDS = "id,text,created_at,conversation_id,lang,note_tweet,referenced_tweets,public_metrics,withheld"
READ_PRICE_USD = 0.005
MIN_RESULTS = 10
MAX_POSTS = 100
MAX_REPLIES = 10
MAX_QUERY = 400
X_SEARCHES = {
    "oyoshi": {"label": "OYOSHI mentions", "query": "OYOSHI -is:retweet -is:reply"},
    "border_tension": {
        "label": "Thailand–Cambodia border tension",
        "query": "(Thailand OR Thai OR ไทย) (Cambodia OR Cambodian OR กัมพูชา OR កម្ពុជា) "
                 "(border OR ชายแดน OR ព្រំដែន) -is:retweet -is:reply",
    },
    "boycott": {
        "label": "Boycott of Thai products in Cambodia",
        "query": "(boycott OR คว่ำบาตร OR ពហិការ) (Thailand OR Thai OR ไทย) "
                 "(Cambodia OR Cambodian OR กัมพูชา OR កម្ពុជា) -is:retweet -is:reply",
    },
}
_request_lock = threading.Lock()
_next_request = 0.0


class XError(RuntimeError):
    def __init__(self, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


def x_configured() -> bool:
    return bool(get_setting("X_BEARER_TOKEN"))


def search_url(query: str) -> str:
    return "https://x.com/search?" + urlencode({"q": query, "src": "typed_query", "f": "live"})


def estimate_reads(search_count: int, max_posts: int, replies_per_post: int) -> int:
    """Upper bound on posts X will return, which is what a recent-search run is billed for."""
    posts = max(0, int(search_count)) * max(MIN_RESULTS, min(MAX_POSTS, int(max_posts)))
    if posts == 0 or int(replies_per_post) <= 0:
        return posts
    # The replies endpoint's smallest page is 10, even when fewer replies are kept.
    # Each of those calls can also return the referenced post beside the replies.
    page = max(MIN_RESULTS, min(MAX_REPLIES, int(replies_per_post)))
    return posts + posts * page * 2


def build_post_query(text: str) -> str:
    query = " ".join(str(text or "").split())
    if not query:
        raise XError("Enter search terms.")
    if len(query) > MAX_QUERY:
        raise XError("Shorten the X search to 400 characters.")
    lowered = query.lower()
    extras = []
    if "is:retweet" not in lowered:
        extras.append("-is:retweet")
    if "is:reply" not in lowered:
        extras.append("-is:reply")
    built = f"{query} {' '.join(extras)}".strip()
    if len(built) > 512:
        raise XError("The X search is too long after required filters.")
    return built


class XClient:
    """Application-only bearer token. The token stays on the server."""

    def __init__(self, deadline: float):
        if not x_configured():
            raise XError("X is not connected. Set X_BEARER_TOKEN on the server.", fatal=True)
        self.deadline = deadline
        self.reads = 0
        self._token = get_setting("X_BEARER_TOKEN")

    def _request(self, params: dict) -> dict:
        global _next_request
        with _request_lock:
            delay = max(0.0, _next_request - time.monotonic())
            if delay > 5:
                raise XError("X rate limit reached. Wait before collecting again.", fatal=True)
            if time.monotonic() + delay + 1 >= self.deadline:
                raise XError("Collection reached its time budget. Run a smaller search to continue.", fatal=True)
            if delay:
                time.sleep(delay)
            _next_request = time.monotonic() + 0.05
            request = Request(
                SEARCH_URL + "?" + urlencode(params),
                headers={"Authorization": f"Bearer {self._token}", "User-Agent": "CrisisRadar/1.0"},
            )
            try:
                with urlopen(request, timeout=min(20, max(1, self.deadline - time.monotonic())),
                             context=ssl.create_default_context(cafile=certifi.where())) as response:
                    remaining = response.headers.get("x-rate-limit-remaining")
                    reset = response.headers.get("x-rate-limit-reset")
                    if remaining is not None and reset:
                        try:
                            if float(remaining) < 1:
                                wait = max(0.0, float(reset) - time.time())
                                if wait > 5:
                                    raise XError("X rate limit reached. Wait before collecting again.", fatal=True)
                                _next_request = max(_next_request, time.monotonic() + wait)
                        except ValueError:
                            pass
                    payload = json.load(response)
            except XError:
                raise
            except HTTPError as exc:
                if exc.code == 429:
                    raise XError("X rate limit reached. Wait before collecting again.", fatal=True) from None
                if exc.code in {401, 403}:
                    raise XError("X denied access. Check the bearer token and that the app can read posts.",
                                 fatal=True) from None
                if exc.code == 402:
                    raise XError("X post reads are unavailable. Check the app's credit balance.", fatal=True) from None
                if exc.code == 400:
                    raise XError("X rejected this search. Check the search terms and try again.") from None
                raise XError(f"X returned HTTP {exc.code}; try again later.") from None
            except (URLError, TimeoutError, json.JSONDecodeError):
                raise XError("X could not be reached or returned an invalid response. Try again later.") from None
        if not isinstance(payload, dict):
            raise XError("X returned an unexpected response.")
        if payload.get("errors") and not payload.get("data"):
            raise XError("X could not run this search. Check the search terms and try again.")
        data = payload.get("data")
        included = (payload.get("includes") or {}).get("tweets") if isinstance(payload.get("includes"), dict) else None
        self.reads += (len(data) if isinstance(data, list) else 0) + (len(included) if isinstance(included, list) else 0)
        return payload

    def search(self, query: str, max_results: int, next_token: str | None = None) -> dict:
        params = {
            "query": query,
            "max_results": str(max(MIN_RESULTS, min(100, int(max_results)))),
            "tweet.fields": TWEET_FIELDS,
            "expansions": "referenced_tweets.id",
        }
        if next_token:
            params["next_token"] = next_token
        return self._request(params)


def _text(tweet: dict) -> str:
    note = tweet.get("note_tweet")
    if isinstance(note, dict) and str(note.get("text") or "").strip():
        return str(note["text"]).strip()
    return str(tweet.get("text") or "").strip()


def _reply_total(tweet: dict) -> int | None:
    metrics = tweet.get("public_metrics")
    if not isinstance(metrics, dict) or isinstance(metrics.get("reply_count"), bool):
        return None
    total = metrics.get("reply_count")
    return total if isinstance(total, int) and total >= 0 else None


def _is_repost(tweet: dict) -> bool:
    refs = tweet.get("referenced_tweets")
    return isinstance(refs, list) and any(isinstance(item, dict) and item.get("type") == "retweeted" for item in refs)


def _record(tweet: dict, *, query: str, category: str, source_type: str,
            parent: dict | None = None) -> dict | None:
    if not isinstance(tweet, dict) or _is_repost(tweet):
        return None
    ident = str(tweet.get("id", ""))
    if not ident.isdigit():
        return None
    text = _text(tweet)
    removed = isinstance(tweet.get("withheld"), dict) and not text
    if not text and not removed:
        return None
    record = {
        "url": f"https://x.com/i/status/{ident}",
        "raw_text": "" if removed else text,
        "source_type": source_type,
        "platform": "x",
        "x_id": ident,
        "created_at": str(tweet.get("created_at") or ""),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "conversation_id": str(tweet.get("conversation_id") or ""),
        "lang": str(tweet.get("lang") or ""),
        "x_query": query,
        "x_category": category,
        "x_categories": [category],
        "comments": [],
        "comment_count": 0,
        "content_removed": removed,
        "target_url": search_url(query),
    }
    total = _reply_total(tweet)
    if source_type == "x_post" and total is not None:
        record["reported_comment_count"] = total
    if parent and not removed:
        record.update(parent_url=parent["url"], parent_text=parent.get("raw_text", ""))
    return record


def _included(payload: dict) -> dict[str, dict]:
    includes = payload.get("includes")
    tweets = includes.get("tweets") if isinstance(includes, dict) else None
    found = {}
    if isinstance(tweets, list):
        for tweet in tweets:
            if isinstance(tweet, dict) and str(tweet.get("id", "")).isdigit():
                found[str(tweet["id"])] = tweet
    return found


def _parent_context(tweet: dict, root: dict, included: dict[str, dict]) -> dict:
    refs = tweet.get("referenced_tweets")
    replied = ""
    if isinstance(refs, list):
        for item in refs:
            if isinstance(item, dict) and item.get("type") == "replied_to":
                replied = str(item.get("id", ""))
                break
    if not replied or replied == root.get("x_id"):
        return root
    parent = included.get(replied)
    text = _text(parent) if parent else ""
    if not text:
        return root
    return {"url": f"https://x.com/i/status/{replied}", "raw_text": text}


def _warning(query: str, label: str, exc: XError) -> dict:
    return {"url": search_url(query), "target_url": label, "source_type": "batch_warning", "raw_text": str(exc)}


def _collect_search(client: XClient, query: str, category: str, max_posts: int, replies_per_post: int,
                    records: list[dict], by_id: dict[str, dict]) -> None:
    built = build_post_query(query)
    token, seen_tokens = None, set()
    found = pages = 0
    while found < max_posts and pages < 5:
        pages += 1
        page = client.search(built, min(100, max(MIN_RESULTS, max_posts - found)), token)
        tweets = page.get("data") if isinstance(page.get("data"), list) else []
        if not tweets:
            break
        for tweet in tweets:
            if found >= max_posts:
                break
            record = _record(tweet, query=query, category=category, source_type="x_post")
            if not record:
                continue
            existing = by_id.get(record["x_id"])
            if existing:
                if category not in existing["x_categories"]:
                    existing["x_categories"].append(category)
                continue
            by_id[record["x_id"]] = record
            records.append(record)
            found += 1
        token = str((page.get("meta") or {}).get("next_token") or "")
        if not token or token in seen_tokens:
            break
        seen_tokens.add(token)
    else:
        if found < max_posts and token:
            raise XError("This search returned more pages than the run will follow. Collected posts were kept.")

    if not replies_per_post:
        return
    parents = [row for row in records if row.get("x_category") == category and row.get("source_type") == "x_post"
               and not row.get("content_removed")]
    for parent in parents:
        if not str(parent.get("x_id", "")).isdigit():
            continue
        page = client.search(f"conversation_id:{parent['x_id']} is:reply -is:retweet", max(MIN_RESULTS, replies_per_post))
        included = _included(page)
        kept = 0
        for tweet in page.get("data") if isinstance(page.get("data"), list) else []:
            if kept >= replies_per_post:
                break
            record = _record(tweet, query=query, category=category, source_type="x_reply",
                             parent=_parent_context(tweet, parent, included))
            if not record or record["x_id"] in by_id:
                continue
            by_id[record["x_id"]] = record
            records.append(record)
            kept += 1
        parent["comment_count"] = kept
        parent["reply_sample_limit"] = replies_per_post


def collect_x(searches: dict[str, str], max_posts: int = 10, replies_per_post: int = 0,
              client: XClient | None = None, on_progress=None) -> list[dict]:
    """Collect deduplicated recent posts. A failed search is kept as a warning."""
    jobs = []
    for key, query in searches.items():
        text = str(query or "").strip()
        if text:
            label = X_SEARCHES.get(key, {}).get("label", key)
            jobs.append((key, label, text))
    if not jobs:
        raise XError("Choose at least one X search.")
    max_posts = max(MIN_RESULTS, min(MAX_POSTS, int(max_posts)))
    replies_per_post = max(0, min(MAX_REPLIES, int(replies_per_post)))
    client = client or XClient(time.monotonic() + 180)
    records, warnings, by_id = [], [], {}
    for index, (key, label, query) in enumerate(jobs, 1):
        try:
            _collect_search(client, query, key, max_posts, replies_per_post, records, by_id)
        except XError as exc:
            warnings.append(_warning(query, label, exc))
            if exc.fatal:
                break
        if on_progress:
            on_progress(index, len(jobs), label)
    return records + warnings
