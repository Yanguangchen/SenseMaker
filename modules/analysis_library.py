"""Select saved records by their collection sources, rather than their language."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

SOURCE_LABELS = {
    "source:facebook": "Facebook", "source:reddit": "Reddit", "source:news": "Online news",
    "source:x": "X", "source:website": "Custom website", "source:other": "Other saved sources",
}


def record_source(post: dict[str, Any]) -> str:
    kind = str(post.get("source_type") or "")
    platform = str(post.get("platform") or "").lower()
    if kind == "website_page" or platform == "website":
        return "source:website"
    if kind == "news_article" or platform == "news":
        return "source:news"
    if kind.startswith("reddit_") or platform == "reddit":
        return "source:reddit"
    if kind in {"x_post", "x_reply"} or platform in {"x", "twitter"}:
        return "source:x"
    if platform == "facebook":
        return "source:facebook"
    for url in (post.get("target_url", ""), post.get("url", "")):
        host = source_key(url).split("/", 1)[0]
        if host in {"facebook.com", "reddit.com", "x.com"}:
            return {"facebook.com": "source:facebook", "reddit.com": "source:reddit", "x.com": "source:x"}[host]
    return "source:other"


def _date(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def dataset_timestamp(post: dict[str, Any]) -> datetime | None:
    # Publication time is not the date a dataset was collected or saved.
    for field in ("saved_at", "scraped_at", "record_created_at"):
        if (value := _date(post.get(field))) is not None:
            return value
    return None


def dataset_date_label(group: dict[str, Any]) -> str:
    date = _date(group.get("timestamp"))
    return date.strftime("%d %b %Y, %H:%M:%S UTC") if date else "Date unavailable"


def dataset_key(post: dict[str, Any]) -> str:
    """Existing custom titles define datasets without migrating stored records."""
    title = str(post.get("custom_title") or "").strip()
    return "title:" + title if title else "untitled:"


def dataset_options(rows: list[dict[str, Any]], source_urls: list[str] | None) -> list[dict[str, Any]]:
    """Summarize all matching records before the user's per-load limit is applied."""
    groups = {}
    for row in select_records(rows, source_urls=source_urls, limit=len(rows)):
        key = dataset_key(row)
        group = groups.setdefault(key, {"key": key, "title": str(row.get("custom_title") or "").strip()
                                       or "Untitled (older records)", "count": 0, "pending": 0,
                                       "timestamp": "", "sources": []})
        group["count"] += 1
        group["pending"] += row.get("status") == "pending"
        timestamp = dataset_timestamp(row)
        if timestamp is not None and (not group["timestamp"] or timestamp > _date(group["timestamp"])):
            group["timestamp"] = timestamp.isoformat()
        source = SOURCE_LABELS[record_source(row)]
        if source not in group["sources"]:
            group["sources"].append(source)
    return sorted(groups.values(), key=lambda group: (group["timestamp"], group["title"]), reverse=True)


def source_key(url: str) -> str:
    """Ignore display-only URL variants while retaining individual post paths."""
    if isinstance(url, str) and url in SOURCE_LABELS:
        return url
    try:
        parts = urlsplit(str(url).strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or not host:
            return ""
        if host == "fb.com" or host == "facebook.com" or host.endswith(".facebook.com"):
            host = "facebook.com"
        elif host == "reddit.com" or host.endswith(".reddit.com"):
            host = "reddit.com"
        elif host in {"x.com", "twitter.com"} or host.endswith(".x.com") or host.endswith(".twitter.com"):
            host = "x.com"
        path = parts.path.rstrip("/")
        # Reddit collection currently covers the r/cambodia feed and its threads.
        if host == "reddit.com" and path.lower().startswith("/r/cambodia"):
            segments = path.split("/")
            if len(segments) > 2 and segments[2].lower() == "cambodia":
                path = "/r/cambodia"
        if host == "x.com":
            segments = [segment for segment in path.split("/") if segment]
            if len(segments) >= 2 and segments[-2].lower() == "status" and segments[-1].isdigit():
                path = "/i/status/" + segments[-1]
        query = "" if host in {"facebook.com", "reddit.com"} or path.startswith("/i/status/") else parts.query
        return host + path + ("?" + query if query else "")
    except ValueError:
        return ""


def matches_sources(post: dict[str, Any], keys: set[str]) -> bool:
    if record_source(post) in keys:
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
    dataset: str | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter before limiting so legacy records cannot crowd out current sources.

    A dataset list selects their union; an empty list selects no records.
    None is an explicit request for the full library. An empty source list selects
    no records; news, X and websites must also be explicitly selected.
    """
    if dataset is not None:
        datasets = {dataset} if isinstance(dataset, str) else set(dataset)
        rows = [row for row in rows if dataset_key(row) in datasets]
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
