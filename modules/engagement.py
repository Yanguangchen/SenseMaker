"""Engagement counts and per-collection checks for velocity tracking.

A check records what CrisisRadar saw when a post was collected: the platform's
comment, view, reaction and share totals where the captured page text or API shows
them, plus how many comments were collected. Counts a page did not show are left out,
never estimated. Reach is not public on any supported platform and is never recorded.
"""

from __future__ import annotations

import re
from typing import Any

STORED_CHECKS = 20
# Comments and news articles have no engagement to follow between collections.
UNTRACKED_TYPES = {"news_article", "reddit_comment", "x_reply"}

_NUMBER = r"\d[\d.,]*(?:\s?[KkMm])?"
_COUNT_PAIR = re.compile(rf"({_NUMBER})\s+(comments?|shares?|views?|plays?)\b", re.I)
# A footer line holds only counts ("1.2K comments · 56 shares"), never a sentence.
_COUNT_LINE = re.compile(rf"(?:{_NUMBER}\s+(?:comments?|shares?|views?|plays?)\s*[·•|]?\s*)+", re.I)
_REACTIONS = re.compile(rf"all reactions:\s*({_NUMBER})\s*$", re.I | re.M)
_KEYS = {"comment": "comments", "share": "shares", "view": "views", "play": "views"}


def parse_count(text: str) -> int | None:
    """Read a displayed count such as 12, 1,234, 1.2K or 3M."""
    value = str(text or "").strip().replace(" ", "")
    if not value:
        return None
    if value[-1] in "KkMm":
        scale = 1000 if value[-1] in "Kk" else 1_000_000
        try:
            return int(round(float(value[:-1].replace(",", ".")) * scale))
        except ValueError:
            return None
    digits = value.replace(",", "").replace(".", "")
    return int(digits) if digits.isdigit() else None


def engagement_from_text(text: Any) -> dict[str, int]:
    """Platform totals from captured post text; the first footer line wins."""
    text = str(text or "")
    found: dict[str, int] = {}
    for line in text.splitlines():
        if not _COUNT_LINE.fullmatch(line.strip()):
            continue
        for number, word in _COUNT_PAIR.findall(line):
            count = parse_count(number)
            key = _KEYS[word.lower().rstrip("s")]
            if count is not None:
                found.setdefault(key, count)
    reactions = _REACTIONS.search(text)
    if reactions and parse_count(reactions.group(1)) is not None:
        found["reactions"] = parse_count(reactions.group(1))
    return found


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def snapshot(post: dict[str, Any], at: str) -> dict[str, Any]:
    check: dict[str, Any] = {"at": at, "collected_comments": _int(post.get("comment_count")) or 0}
    check.update(engagement_from_text(post.get("raw_text", "")))
    reported = _int(post.get("reported_comment_count"))
    if reported is not None:
        check["comments"] = reported
    return check


def is_tracked(post: dict[str, Any]) -> bool:
    return post.get("source_type") not in UNTRACKED_TYPES


def _valid(checks: Any) -> list[dict[str, Any]]:
    if not isinstance(checks, list):
        return []
    return sorted((c for c in checks if isinstance(c, dict) and isinstance(c.get("at"), str) and c["at"]),
                  key=lambda c: c["at"])


def checks_for(post: dict[str, Any]) -> list[dict[str, Any]]:
    """Saved checks, or the original collection as the first check for older records."""
    checks = _valid(post.get("checks"))
    if checks or not is_tracked(post):
        return checks
    observed = post.get("scraped_at") or post.get("saved_at")
    return [snapshot(post, observed)] if isinstance(observed, str) and observed else []


def add_check(previous: dict[str, Any], post: dict[str, Any], at: str) -> list[dict[str, Any]]:
    """Append this collection to a record's checks; saving the same collection again replaces it."""
    by_time = {check["at"]: check for check in checks_for(previous)} if previous else {}
    by_time[at] = snapshot(post, at)
    return sorted(by_time.values(), key=lambda c: c["at"])[-STORED_CHECKS:]


def velocity(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Comment growth between the latest checks, with the template's Live/Slowing/Dead status.

    Live: still gaining at least as fast as before. Slowing: gains shrinking.
    Dead: no growth across the latest checks. One check has no velocity yet.
    """
    metric = "comments" if any(_int(c.get("comments")) is not None for c in checks) else "collected_comments"
    points = [(c["at"], _int(c.get(metric))) for c in checks]
    points = [(at, value) for at, value in points if value is not None]
    values = [value for _, value in points]
    result: dict[str, Any] = {"metric": metric, "points": points, "current": values[-1] if values else None,
                              "prior": None, "change": None, "pct": None, "direction": None, "status": None}
    if len(values) < 2:
        return result
    change = values[-1] - values[-2]
    earlier = values[-2] - values[-3] if len(values) > 2 else None
    if change > 0:
        status = "Live" if earlier is None or change >= earlier else "Slowing"
    else:
        status = "Slowing" if earlier is not None and earlier > 0 else "Dead"
    result.update(prior=values[-2], change=change, pct=100 * change / values[-2] if values[-2] else None,
                  direction="up" if change > 0 else "down" if change < 0 else "flat", status=status)
    return result


def latest_pair(checks: list[dict[str, Any]], key: str) -> tuple[int | None, int | None]:
    """The newest and previous values of one count, skipping checks that lack it."""
    values = [value for value in (_int(c.get(key)) for c in checks) if value is not None]
    return (values[-1] if values else None, values[-2] if len(values) > 1 else None)
