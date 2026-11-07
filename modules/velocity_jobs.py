"""Account-scoped velocity workers, independent of Streamlit page reruns."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from secrets import token_hex
import threading

from modules import database, velocity_tracking as store
from modules.collection_jobs import active_collection
from modules.facebook_session import FacebookLoginError
from modules.saved_links import library_owner_id
from modules.velocity_scraper import collect_views

log = logging.getLogger(__name__)
_lock = threading.Lock()
_jobs: dict[str, "VelocityJob"] = {}
BUSY = {"running", "saving", "save_failed"}


@dataclass
class VelocityJob:
    id: str
    card_id: str
    posts: list[dict]
    status: str = "running"
    completed: int = 0
    message: str = "Connecting to Facebook…"
    error: str = ""
    run: dict = field(default_factory=dict)


def latest_job(email: str) -> VelocityJob | None:
    with _lock:
        return _jobs.get(library_owner_id(email))


def _save(email: str, job: VelocityJob) -> None:
    try:
        store.save_run(database.init_db(), email, job.card_id, job.run)
    except Exception as exc:
        log.error("Velocity check could not be saved (%s)", type(exc).__name__)
        job.error = "The check could not be saved to Firestore. Retry saving to keep these results without scraping again."
        job.status = "save_failed"
    else:
        job.error = ""
        job.status = "complete"


def start_run(email: str, card_id: str, *, hosted: bool) -> VelocityJob:
    """Reload the saved links and start at most one pending run per account."""
    with _lock:
        owner = library_owner_id(email)
        previous = _jobs.get(owner)
        if previous and previous.status in BUSY:
            raise ValueError("Finish the current velocity run or save its results before starting another.")
        if active_collection(email):
            raise ValueError("Wait for the current Facebook collection to finish, then run this card.")
        cards = store.load_cards(database.init_db(), email)
        card = next((row for row in cards if row["id"] == card_id), None)
        posts = deepcopy(store.card_posts(card)) if card else []
        if not posts:
            raise ValueError("Add a Facebook post to this card before running it.")
        job = VelocityJob(token_hex(32), card_id, posts)
        job.run = {"id": job.id, "started_at": datetime.now(timezone.utc).isoformat()}
        _jobs[owner] = job

    def progress(completed: int, message: str) -> None:
        job.completed, job.message = completed, message

    def worker() -> None:
        values = []

        def partial(rows: list[dict]) -> None:
            values[:] = rows

        try:
            async def collect():
                async with asyncio.timeout(720):
                    return await collect_views(email, job.posts, hosted=hosted, progress=progress, partial=partial)
            values = asyncio.run(collect())
        except Exception as exc:
            log.warning("Velocity run failed (%s)", type(exc).__name__)
            message = str(exc) if isinstance(exc, FacebookLoginError) else "The Facebook browser could not finish this run."
            if not values:
                job.error = message + " No check was added. Please try again."
                job.status = "error"
                return
            job.run["warning"] = message + " Completed counts were saved; run again to retry failed posts."
            checked = {row["post_id"] for row in values}
            values.extend({"post_id": post["id"], "url": post["url"], "views": None, "outcome": "error",
                           "reason": "The browser stopped before this post could be checked."}
                          for post in job.posts if post["id"] not in checked)
        job.run.update(at=datetime.now(timezone.utc).isoformat(), results=values)
        job.status = "saving"
        job.message = "Saving check to Firestore…"
        _save(email, job)

    threading.Thread(target=worker, name="velocity-check", daemon=True).start()
    return job


def retry_save(email: str, job_id: str) -> None:
    with _lock:
        job = _jobs.get(library_owner_id(email))
        if job is None or job.id != job_id or job.status != "save_failed":
            return
        job.status = "saving"
    threading.Thread(target=_save, args=(email, job), name="velocity-save", daemon=True).start()
