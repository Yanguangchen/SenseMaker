"""Select saved records by their collection sources, rather than their language."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


def source_key(url: str) -> str:
    """Ignore display-only URL variants while retaining individual post paths."""
    try:
        parts = urlsplit(str(url).strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or not host:
            return ""
        if host == "fb.com" or host == "facebook.com" or host.endswith(".facebook.com"):
            host = "facebook.com"
        elif host == "reddit.com" or host.endswith(".reddit.com"):
            host = "reddit.com"
        path = parts.path.rstrip("/")
        # Reddit collection currently covers the r/cambodia feed and its threads.
        if host == "reddit.com" and path.lower().startswith("/r/cambodia"):
            segments = path.split("/")
            if len(segments) > 2 and segments[2].lower() == "cambodia":
                path = "/r/cambodia"
        query = "" if host in {"facebook.com", "reddit.com"} else parts.query
        return host + path + ("?" + query if query else "")
    except ValueError:
        return ""


def matches_sources(post: dict[str, Any], keys: set[str]) -> bool:
    # Saved news articles come from the app's current Online News searches.
    if post.get("source_type") == "news_article":
        return True
    for url in (post.get("target_url", ""), post.get("url", "")):
        key = source_key(url)
        if key and key in keys:
            return True
        if key.startswith("facebook.com/groups/"):
            group = "/".join(key.split("/")[:3])
            if group in keys:
                return True
    return False


def _collected_at(post: dict[str, Any]) -> datetime:
    for field in ("scraped_at", "saved_at", "created_at"):
        value = post.get(field)
        try:
            value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        except (TypeError, ValueError):
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def select_records(
    rows: list[dict[str, Any]], *, source_urls: list[str] | None, limit: int,
) -> list[dict[str, Any]]:
    """Filter before limiting so legacy records cannot crowd out current sources.

    None is an explicit request for the full library. An empty source list never
    widens access to older Facebook sources; saved Online News remains available.
    """
    if source_urls is not None:
        keys = {key for url in source_urls if (key := source_key(url))}
        rows = [row for row in rows if matches_sources(row, keys)]
    return sorted(rows, key=lambda row: (_collected_at(row), str(row.get("_id", ""))),
                  reverse=True)[:limit]


def select_history(rows: list[dict[str, Any]], source_urls: list[str] | None) -> list[dict[str, Any]]:
    """A mixed or unattributed run cannot represent the current source selection."""
    if source_urls is None:
        return rows
    keys = {key for url in source_urls if (key := source_key(url))}
    return [row for row in rows if row.get("record_sources")
            and all(matches_sources(source, keys) for source in row["record_sources"])]
