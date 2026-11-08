"""Unattended daily velocity checks that need no open dashboard session.

One worker thread per container wakes on a short tick, asks Firestore which cards
have not been checked yet for the current scheduled day, and runs them through the
same worker the Run button uses. Progress therefore appears in the notepad, and a
manual run always wins: an automatic one is deferred to a later tick instead of
scraping the same posts twice. Each card's outcome is recorded per scheduled day,
so a container restart resumes the day's remaining checks without repeating the
ones already saved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
import logging
import os
import threading
from typing import Any

from modules import database, velocity_jobs as jobs, velocity_tracking as store
from modules.config import get_setting
from modules.saved_links import library_owner_id

log = logging.getLogger(__name__)

COLLECTION = "sensemaker_velocity_schedule"
DEFAULT_HOUR = 2
MAX_ATTEMPTS = 3
RETRY_SECONDS = 30 * 60
TICK_SECONDS = 5 * 60
STARTUP_SECONDS = 30
FALSE_VALUES = {"0", "false", "no", "off"}

_start_lock = threading.Lock()
_stop: threading.Event | None = None
_thread: threading.Thread | None = None


def hosted_runtime() -> bool:
    """Match the dashboard's hosted detection from a worker with no session."""
    return os.path.exists("/mount/src") or os.getenv("SENSEMAKER_HOSTED") == "1"


def enabled() -> bool:
    return get_setting("VELOCITY_DAILY_CHECKS", "1").strip().lower() not in FALSE_VALUES


def schedule_zone() -> tzinfo:
    """Read the configured zone, falling back to UTC when tzdata is unavailable."""
    name = (get_setting("VELOCITY_DAILY_TIMEZONE", "UTC") or "UTC").strip()
    if name.upper() == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        log.warning("VELOCITY_DAILY_TIMEZONE %r is unavailable; daily checks use UTC.", name)
        return timezone.utc


def zone_label() -> str:
    zone = schedule_zone()
    return "UTC" if zone is timezone.utc else str(zone)


def schedule_hour() -> int:
    raw = get_setting("VELOCITY_DAILY_HOUR", str(DEFAULT_HOUR)).strip()
    try:
        hour = int(raw)
    except ValueError:
        hour = -1
    if not 0 <= hour <= 23:
        log.warning("VELOCITY_DAILY_HOUR %r is not an hour from 0 to 23; using %s.", raw, DEFAULT_HOUR)
        return DEFAULT_HOUR
    return hour


def now_local() -> datetime:
    return datetime.now(schedule_zone())


def due_time(moment: datetime) -> datetime:
    """The scheduled moment on the calendar day of ``moment``."""
    return moment.replace(hour=schedule_hour(), minute=0, second=0, microsecond=0)


def next_run_at(moment: datetime | None = None) -> datetime:
    moment = moment or now_local()
    scheduled = due_time(moment)
    return scheduled if moment < scheduled else scheduled + timedelta(days=1)


def schedule_caption(moment: datetime | None = None) -> str:
    """One line for the workspace header, so the cadence is never a mystery."""
    if not enabled():
        return "Automatic daily checks are turned off for this deployment."
    scheduled = next_run_at(moment)
    return (f"Every card with posts is checked automatically at {schedule_hour():02}:00 {zone_label()} · "
            f"next on {scheduled.strftime('%d %b at %H:%M')}")


def _state_ref(db: Any, owner_id: str) -> Any:
    return db.collection(COLLECTION).document(owner_id)


def load_state(db: Any, owner_id: str) -> dict[str, dict[str, Any]]:
    """Read one account's per-card record of the last automatic check."""
    record = _state_ref(db, owner_id).get(timeout=15, retry=None).to_dict() or {}
    cards = record.get("cards")
    return cards if isinstance(cards, dict) else {}


def _write_state(db: Any, owner_id: str, records: dict[str, dict[str, Any]]) -> None:
    """Record progress after every card; a lost write can only repeat one check."""
    try:
        _state_ref(db, owner_id).set({"cards": records})
    except Exception:
        log.exception("The daily velocity schedule could not be saved")


def due_cards(records: dict[str, dict[str, Any]], cards: list[dict[str, Any]],
              moment: datetime) -> list[dict[str, Any]]:
    """Cards whose check for ``moment``'s scheduled day is still outstanding."""
    if moment < due_time(moment):
        return []
    today = moment.date().isoformat()
    due = []
    for card in cards:
        if card.get("shared") or not store.card_posts(card):
            continue  # Only the creator's own login may run a card.
        record = records.get(card["id"], {})
        if record.get("date") != today:
            due.append(card)
        elif record.get("blocked"):
            # No browser time was spent, so retry on every tick: the check resumes
            # within five minutes of somebody reconnecting Facebook.
            due.append(card)
        elif (record.get("status") == "error" and record.get("attempts", 0) < MAX_ATTEMPTS
              and moment.timestamp() - record.get("attempted_at", 0) >= RETRY_SECONDS):
            due.append(card)  # A bounded retry, so one bad day cannot scrape all night.
    return due


def _facebook_blocked(email: str, *, hosted: bool) -> str:
    """Explain a missing hosted login instead of spending browser minutes on it."""
    if not hosted:
        return ""
    try:
        from modules.facebook_remote import remote_facebook_configured, remote_facebook_session

        if not remote_facebook_configured():
            return "The hosted Facebook browser is not configured, so daily checks cannot run."
        if remote_facebook_session(email).status == "disconnected":
            return ("Facebook is not connected. Open Scrape Data and connect Facebook "
                    "to resume automatic daily checks.")
    except Exception:
        log.exception("The saved Facebook profile could not be read")
    return ""


def run_due(db: Any, email: str, *, hosted: bool, moment: datetime | None = None) -> dict[str, int]:
    """Run one account's outstanding checks, one card at a time."""
    moment = moment or now_local()
    owner = library_owner_id(email)
    cards = store.load_cards(db, email)
    known = {card["id"] for card in cards}
    records = {card_id: row for card_id, row in load_state(db, owner).items() if card_id in known}
    due = due_cards(records, cards, moment)
    summary = {"ran": 0, "failed": 0, "deferred": 0}
    if not due:
        return summary
    pending = jobs.latest_job(email)
    if pending is not None and pending.status == "save_failed":
        jobs.save_now(email, pending.id)  # Never let yesterday's failed save block today.
    blocked = _facebook_blocked(email, hosted=hosted)
    if blocked:
        log.warning("Daily velocity checks cannot run: %s", blocked)
    today = moment.date().isoformat()
    for card in due:
        previous = records.get(card["id"], {})
        attempt = {"date": today, "attempted_at": moment.timestamp(),
                   "attempts": previous.get("attempts", 0) + 1 if previous.get("date") == today else 1}
        if blocked:
            summary["failed"] += 1
            if (previous.get("blocked") and previous.get("error") == blocked
                    and previous.get("date") == today):
                continue  # Already recorded for today; do not rewrite it every tick.
            records[card["id"]] = {**attempt, "status": "error", "error": blocked, "blocked": True}
        else:
            try:
                job = jobs.run_card(email, card["id"], hosted=hosted)
            except ValueError as exc:
                # A manual run, a running collection, or a card emptied meanwhile.
                log.info("Daily velocity check deferred (%s)", exc)
                summary["deferred"] += 1
                continue
            except Exception:
                log.exception("Daily velocity check failed")
                summary["failed"] += 1
                records[card["id"]] = {**attempt, "status": "error",
                                       "error": "The daily check could not run. It will be retried."}
            else:
                failed = job.status != "complete"
                summary["failed" if failed else "ran"] += 1
                records[card["id"]] = {**attempt, "run_id": job.id,
                                       "status": "error" if failed else "complete",
                                       "error": job.error if failed else job.run.get("warning", "")}
        _write_state(db, owner, records)
    return summary


def tick(*, hosted: bool | None = None, moment: datetime | None = None) -> dict[str, int]:
    """One scheduler pass over every approved account that owns cards."""
    totals = {"ran": 0, "failed": 0, "deferred": 0}
    if not enabled():
        return totals
    moment = moment or now_local()
    if moment < due_time(moment):
        return totals  # Nothing can be due before the scheduled hour; read nothing.
    from modules.auth import allowed_emails

    owners = sorted(allowed_emails())
    if not owners:
        return totals  # No approved accounts means nothing to read from Firestore.
    hosted = hosted_runtime() if hosted is None else hosted
    try:
        db = database.init_db()
    except Exception:
        log.exception("Daily velocity checks could not reach Firestore")
        return totals
    for email in owners:
        try:
            summary = run_due(db, email, hosted=hosted, moment=moment)
        except Exception:
            log.exception("Daily velocity checks could not run for one account")
            continue
        for name, value in summary.items():
            totals[name] += value
    return totals


def _loop(stop: threading.Event) -> None:
    """Tick until this worker's own event is set, so a replaced worker exits."""
    try:
        from modules.config import load_environment

        # A container start reaches this before any session runs the dashboard, so
        # local .env values are read here too. Real environment values still win.
        load_environment()
    except Exception:
        log.exception("Settings could not be loaded for daily velocity checks")
    stop.wait(STARTUP_SECONDS)
    while not stop.is_set():
        try:
            totals = tick()
        except Exception:
            log.exception("The daily velocity check cycle failed")
        else:
            if any(totals.values()):
                log.info("Daily velocity checks: %s", totals)
        stop.wait(TICK_SECONDS)


def ensure_scheduler_started() -> bool:
    """Start this process's one daily worker; safe to call on every Streamlit rerun."""
    global _stop, _thread
    if not enabled():
        return False
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop = threading.Event()
        _thread = threading.Thread(target=_loop, args=(_stop,), name="velocity-daily", daemon=True)
        _thread.start()
        log.info("Automatic daily velocity checks are scheduled for %02d:00 %s", schedule_hour(), zone_label())
        return True


def stop_scheduler() -> None:
    """Used by tests. A container simply exits with the daemon thread."""
    global _stop, _thread
    with _start_lock:
        if _stop is not None:
            _stop.set()
        _stop, _thread = None, None


def attach_status(db: Any, email: str, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach each card's last automatic result, including a teammate's own cards."""
    viewer = library_owner_id(email)
    states: dict[str, dict[str, Any]] = {}
    attached = []
    for card in cards:
        owner = card.get("owner_id") if card.get("shared") else viewer
        card_id = card.get("source_card_id") if card.get("shared") else card.get("id")
        if owner not in states:
            try:
                states[owner] = load_state(db, owner)
            except Exception:
                log.exception("The daily velocity schedule could not be read")
                states[owner] = {}
        record = states[owner].get(card_id)
        attached.append({**card, "daily": record} if isinstance(record, dict) else card)
    return attached


def daily_notice(card: dict[str, Any]) -> str:
    """The reason the last automatic check saved no column, for display."""
    record = card.get("daily") or {}
    if record.get("status") != "error":
        return ""
    return str(record.get("error") or "The automatic daily check could not run.")
