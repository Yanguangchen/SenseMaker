"""Per-account subscriptions to native notifications for automatic velocity checks.

Both the subscription and the notifications a check leaves behind live in
Firestore, so a run at 02:00 can still reach a browser that opens hours later.
Delivery is the browser's own Notification API, in the installed app or an open
tab: no push service, no credentials, and nothing is sent anywhere on the user's
behalf. Each notification is queued once per card run and shown once.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import logging
from secrets import token_hex
from typing import Any

from firebase_admin import firestore

from modules.saved_links import library_owner_id
from modules.velocity_metrics import METRIC, engagement_count
from modules.velocity_table import engagement_change_percent, engagement_status
from modules.velocity_tracking import card_posts

log = logging.getLogger(__name__)

COLLECTION = "sensemaker_velocity_alerts"
MAX_PENDING = 30
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 300
DEFAULT_CARD_NAME = "Velocity tracking card"
# Reported worst first, matching the table's own wording.
STATUS_ORDER = ("Viral", "dying", "dead", "alive")


def _ref(db: Any, email: str) -> Any:
    return db.collection(COLLECTION).document(library_owner_id(email))


def is_subscribed(db: Any, email: str) -> bool:
    return bool((_ref(db, email).get(timeout=15, retry=None).to_dict() or {}).get("subscribed"))


def set_subscription(db: Any, email: str, subscribed: bool) -> bool:
    """Save the choice, keeping notifications already waiting; clear them on opt-out."""
    ref = _ref(db, email)
    subscribed = bool(subscribed)

    @firestore.transactional
    def save(transaction: Any) -> bool:
        record = ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}
        pending = _queued(record) if subscribed else []
        transaction.set(ref, {"subscribed": subscribed, "pending": pending,
                              "updated_at": datetime.now(timezone.utc).isoformat()})
        return subscribed

    return save(db.transaction(max_attempts=3))


def _queued(record: dict[str, Any]) -> list[dict[str, Any]]:
    pending = record.get("pending")
    return [row for row in pending if isinstance(row, dict) and row.get("id")] if isinstance(pending, list) else []


def take_pending(db: Any, email: str) -> list[dict[str, Any]]:
    """Read and clear the queue, so one check is never announced twice."""
    ref = _ref(db, email)

    @firestore.transactional
    def take(transaction: Any) -> list[dict[str, Any]]:
        record = ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}
        pending = _queued(record)
        if pending:
            transaction.set(ref, {**record, "pending": []})
        return pending

    return take(db.transaction(max_attempts=3))


def card_title(card: dict[str, Any]) -> str:
    name = str(card.get("name") or DEFAULT_CARD_NAME).strip() or DEFAULT_CARD_NAME
    return name[:MAX_TITLE_CHARS]


def _comparable_runs(card: dict[str, Any], run: dict[str, Any]) -> list[dict[str, Any]]:
    """Earlier view-count checks are never compared with engagement totals."""
    return [*(old for old in card.get("runs", []) if old.get("metric") == METRIC), run]


def _series(card: dict[str, Any], run: dict[str, Any]) -> dict[str, list[int | None]]:
    """One value per saved run for each post, in the table's own order."""
    history = _comparable_runs(card, run)
    series = {}
    for post in card_posts(card):
        values = []
        for old in history:
            rows = old.get("results", [])
            row = next((item for item in rows if isinstance(item, dict) and item.get("post_id") == post["id"]), None)
            values.append(engagement_count(row))
        series[post["id"]] = values
    return series


def _total_change(series: dict[str, list[int | None]]) -> float | None:
    """Compare only posts counted in both the latest and the previous run."""
    pairs = [(values[-2], values[-1]) for values in series.values()
             if len(values) >= 2 and values[-1] is not None and values[-2] is not None]
    if len(series) == 1:
        # One post reads exactly like its table cell, including the zero rule.
        return engagement_change_percent(next(iter(series.values())))
    previous = sum(before for before, _ in pairs)
    if not pairs or previous == 0:
        return None
    return 100 * (sum(after for _, after in pairs) - previous) / previous


def _status_summary(statuses: list[str]) -> str:
    counts = Counter(statuses)
    if len(statuses) == 1:
        return statuses[0]
    return " · ".join(f"{counts[name]} {name}" for name in STATUS_ORDER if counts.get(name))


def run_body(card: dict[str, Any], run: dict[str, Any]) -> str:
    """Describe one saved check: totals, change, status, and what was missing."""
    series = _series(card, run)
    if not series:
        return "This card has no posts."
    latest = [values[-1] for values in series.values()]
    available = [value for value in latest if value is not None]
    parts = []
    if available:
        parts.append(f"{sum(available):,} likes + comments")
    change = _total_change(series)
    if change is not None:
        parts.append(f"{change:+.1f}%")
    statuses = [status for status in (engagement_status(values) for values in series.values()) if status]
    if statuses:
        parts.append(_status_summary(statuses))
    missing = len(latest) - len(available)
    if missing:
        parts.append(f"{missing} of {len(latest)} posts unavailable"
                     if available else f"no counts read for {missing} posts")
    if run.get("warning"):
        parts.append("saved with gaps")
    return (" · ".join(parts) or "No counts could be read.")[:MAX_BODY_CHARS]


def notification(card: dict[str, Any], *, run: dict[str, Any] | None = None, error: str = "",
                 at: str = "") -> dict[str, Any]:
    """Build the queued record for one finished or failed automatic check."""
    title = card_title(card)
    if run is not None:
        body, status = run_body(card, run), "complete"
    else:
        title, status = f"{title} · check failed"[:MAX_TITLE_CHARS], "error"
        body = (str(error).strip() or "The automatic daily check could not run.")[:MAX_BODY_CHARS]
    return {"id": token_hex(16), "card_id": card.get("id", ""), "card": card_title(card), "title": title,
            "body": body, "status": status, "at": at or datetime.now(timezone.utc).isoformat()}


def record_run(db: Any, email: str, card: dict[str, Any], *, run: dict[str, Any] | None = None,
               error: str = "") -> bool:
    """Queue one notification for a subscribed account; silent for everyone else."""
    ref = _ref(db, email)
    row = notification(card, run=run, error=error)

    @firestore.transactional
    def save(transaction: Any) -> bool:
        record = ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}
        if not record.get("subscribed"):
            return False
        # Bounded: a browser that stays closed for weeks keeps the newest only.
        transaction.set(ref, {**record, "pending": [*_queued(record), row][-MAX_PENDING:]})
        return True

    return save(db.transaction(max_attempts=3))
