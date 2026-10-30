"""Read-only r/cambodia collection through Reddit's authenticated Data API."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from modules.config import get_setting

CAMBODIA_URL = "https://www.reddit.com/r/cambodia/"
REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "oauth.reddit.com"}
_request_lock = threading.Lock()
_next_request = 0.0


class RedditError(RuntimeError):
    pass


def is_reddit_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in REDDIT_HOSTS or host == "redd.it"


def reddit_configured() -> bool:
    return all(get_setting(k) for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"))


def parse_reddit_source(url: str) -> tuple[str, dict, str | None]:
    """Allow only explicit r/cambodia listings/searches/threads on Reddit hosts."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in REDDIT_HOSTS:
        raise RedditError("Use a full https://www.reddit.com/r/cambodia/ link.")
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise RedditError("Invalid Reddit source URL.")
    parts = parsed.path.strip("/").removesuffix(".json").strip("/").split("/")
    if len(parts) < 2 or parts[0].lower() != "r" or parts[1].lower() != "cambodia":
        raise RedditError("This Reddit connector currently supports r/cambodia links.")
    params = {"raw_json": 1}
    mode = parts[2].lower() if len(parts) > 2 else "new"
    if mode == "comments" and len(parts) >= 4 and re.fullmatch(r"[a-z0-9]+", parts[3]):
        return f"/r/cambodia/comments/{parts[3]}", params, parts[3]
    if len(parts) > 3 or mode not in {"new", "hot", "top", "search"}:
        raise RedditError("Use r/cambodia, its new/hot/top/search feed, or a post link.")
    query = parse_qs(parsed.query)
    if mode == "search":
        q = query.get("q", [""])[0].strip()
        if not q:
            raise RedditError("A Reddit search link needs a search query.")
        params.update(q=q[:500], restrict_sr="on", sort="new")
    if mode in {"top", "search"}:
        period = query.get("t", ["all"])[0]
        params["t"] = period if period in {"hour", "day", "week", "month", "year", "all"} else "all"
    return f"/r/cambodia/{mode}", params, None


class RedditClient:
    """Application-only OAuth, or an approved read-scope refresh token."""

    def __init__(self, deadline: float):
        if not reddit_configured():
            raise RedditError("Reddit is not connected. Configure approved Reddit API credentials on the server.")
        self.deadline = deadline
        self.token = ""
        self.user_agent = get_setting("REDDIT_USER_AGENT")

    def _request(self, request: Request) -> object:
        global _next_request
        with _request_lock:
            delay = max(0, _next_request - time.monotonic())
            if delay > 5:
                raise RedditError("Reddit rate-limit cooldown is active. Try collecting again later.")
            if time.monotonic() + delay + 1 >= self.deadline:
                raise RedditError("Collection reached its time budget. Run a smaller batch to continue.")
            if delay:
                time.sleep(delay)
            _next_request = time.monotonic() + 1.1
            try:
                with urlopen(request, timeout=min(20, self.deadline - time.monotonic())) as response:
                    remaining = response.headers.get("x-ratelimit-remaining")
                    reset = response.headers.get("x-ratelimit-reset")
                    if remaining and reset:
                        try:
                            if float(remaining) < 2:
                                _next_request = max(_next_request, time.monotonic() + float(reset))
                        except ValueError:
                            pass
                    return json.load(response)
            except HTTPError as exc:
                if exc.code == 429:
                    try:
                        retry = max(1, float(exc.headers.get("Retry-After", "60")))
                    except ValueError:
                        retry = 60
                    _next_request = max(_next_request, time.monotonic() + retry)
                    raise RedditError("Reddit rate limit reached. Wait before collecting again.") from None
                if exc.code in {401, 403}:
                    raise RedditError("Reddit denied access. Check the app's approval, credentials and read permissions.") from None
                if exc.code == 404:
                    raise RedditError("This Reddit source is unavailable or was removed.") from None
                raise RedditError(f"Reddit returned HTTP {exc.code}; try again later.") from None
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise RedditError("Reddit could not be reached or returned an invalid response. Try again later.") from exc

    def get(self, path: str, params: dict) -> object:
        if not self.token:
            auth = base64.b64encode(
                f"{get_setting('REDDIT_CLIENT_ID')}:{get_setting('REDDIT_CLIENT_SECRET')}".encode()
            ).decode()
            refresh = get_setting("REDDIT_REFRESH_TOKEN")
            form = {"grant_type": "refresh_token", "refresh_token": refresh} if refresh else {"grant_type": "client_credentials"}
            result = self._request(Request(
                "https://www.reddit.com/api/v1/access_token", data=urlencode(form).encode(),
                headers={"Authorization": f"Basic {auth}", "User-Agent": self.user_agent,
                         "Content-Type": "application/x-www-form-urlencoded"},
            ))
            if not isinstance(result, dict) or not result.get("access_token"):
                raise RedditError("Reddit did not grant an access token. Check approved API access.")
            self.token = result["access_token"]
        return self._request(Request(
            "https://oauth.reddit.com" + path + "?" + urlencode(params),
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": self.user_agent},
        ))


def _children(listing: object) -> list:
    if not isinstance(listing, dict) or not isinstance(listing.get("data"), dict):
        raise RedditError("Reddit returned an unexpected listing.")
    children = listing["data"].get("children")
    if not isinstance(children, list):
        raise RedditError("Reddit returned an unexpected listing.")
    return children


def _record(data: dict, kind: str, parent: dict | None = None) -> dict | None:
    if str(data.get("subreddit", "")).lower() != "cambodia":
        return None
    ident = str(data.get("id", ""))
    if not re.fullmatch(r"[a-z0-9]+", ident):
        return None
    if kind == "t3":
        permalink = f"/r/cambodia/comments/{ident}/"
        body = str(data.get("selftext", ""))
        text = str(data.get("title", "")).strip() + ("\n\n" + body if body else "")
    else:
        if not parent:
            return None
        permalink = f"/r/cambodia/comments/{parent['reddit_id'][3:]}/_/{ident}/"
        body = text = str(data.get("body", ""))
    removed = body.strip() in {"[deleted]", "[removed]"} or bool(data.get("removed_by_category"))
    if not text.strip() and not removed:
        return None
    try:
        created = datetime.fromtimestamp(float(data["created_utc"]), tz=timezone.utc).isoformat()
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        created = ""
    record = {
        "url": "https://www.reddit.com" + permalink,
        "raw_text": "" if removed else text.strip(),
        "source_type": "reddit_post" if kind == "t3" else "reddit_comment",
        "platform": "reddit", "subreddit": "cambodia", "reddit_id": f"{kind}_{ident}",
        "created_at": created, "scraped_at": datetime.now(timezone.utc).isoformat(),
        "comments": [], "comment_count": 0, "content_removed": removed,
    }
    if parent and not removed:
        record.update(parent_url=parent["url"], parent_text=parent["raw_text"],
                      thread_id=parent["reddit_id"])
    return record


def collect_reddit(url: str, max_posts: int = 50, comments_per_post: int = 20,
                   client: RedditClient | None = None) -> list[dict]:
    """Return deduplicated posts and sampled comments; preserve partial progress."""
    path, params, thread_id = parse_reddit_source(url)
    max_posts = max(1, min(500, int(max_posts)))
    comments_per_post = max(0, min(100, int(comments_per_post)))
    client = client or RedditClient(time.monotonic() + 180)
    records, seen, after_seen = [], set(), set()
    post_count = 0

    def add(record):
        if record and record["reddit_id"] not in seen:
            seen.add(record["reddit_id"])
            records.append(record)

    def add_comments(listing, parent):
        count, stack = 0, list(reversed(_children(listing)))
        while stack and count < comments_per_post:
            child = stack.pop()
            if child.get("kind") != "t1":
                continue  # 'more' stubs are not comments; this is a bounded sample.
            data = child.get("data", {})
            record = _record(data, "t1", parent)
            if record and record["reddit_id"] not in seen:
                add(record)
                count += 1
            replies = data.get("replies")
            if isinstance(replies, dict):
                stack.extend(reversed(_children(replies)))
        parent["comment_count"] = count
        parent["comment_sample_limit"] = comments_per_post

    def thread(data):
        if not isinstance(data, list) or len(data) != 2:
            raise RedditError("Reddit returned an unexpected discussion.")
        posts = [p for p in _children(data[0]) if p.get("kind") == "t3"]
        if not posts:
            return
        parent = _record(posts[0].get("data", {}), "t3")
        add(parent)
        if parent and comments_per_post:
            add_comments(data[1], parent)

    try:
        if thread_id:
            thread(client.get(path, {**params, "limit": max(1, comments_per_post), "sort": "new"}))
        else:
            while post_count < max_posts:
                listing = client.get(path, {**params, "limit": min(100, max_posts - post_count)})
                children = _children(listing)
                if not children:
                    break
                for child in children:
                    if child.get("kind") != "t3":
                        continue
                    data = child.get("data", {})
                    parent = _record(data, "t3")
                    if not parent or parent["reddit_id"] in seen:
                        continue
                    add(parent)
                    post_count += 1
                    if comments_per_post and not parent["content_removed"]:
                        details = client.get(f"/r/cambodia/comments/{data['id']}",
                                             {"limit": comments_per_post, "sort": "new", "raw_json": 1})
                        if not isinstance(details, list) or len(details) != 2:
                            raise RedditError("Reddit returned an unexpected discussion.")
                        add_comments(details[1], parent)
                    if post_count >= max_posts:
                        break
                after = listing["data"].get("after")
                if not after or after in after_seen:
                    break
                after_seen.add(after)
                params["after"] = after
    except RedditError as exc:
        if not records:
            raise
        records.append({"url": url, "source_type": "batch_warning", "raw_text": str(exc)})
    for record in records:
        record["target_url"] = url
    return records


async def scrape_reddit(url: str, max_posts: int = 50, comments_per_post: int = 20) -> list[dict]:
    return await asyncio.to_thread(collect_reddit, url, max_posts, comments_per_post)
