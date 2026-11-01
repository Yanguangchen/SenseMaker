"""Owner-scoped collection workers that survive Streamlit reruns and tab changes."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

from modules.saved_links import library_owner_id

ACTIVE_STATUSES = {"running", "cancelling"}


@dataclass
class CollectionJob:
    id: str
    urls: tuple[str, ...]
    status: str = "running"
    completed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)


_jobs: dict[tuple[str, str], CollectionJob] = {}
_lock = threading.Lock()


def collection_job(email: str, job_id: str) -> CollectionJob | None:
    with _lock:
        return _jobs.get((library_owner_id(email), job_id))


def active_collection(email: str) -> CollectionJob | None:
    owner = library_owner_id(email)
    with _lock:
        return next((job for (account, _), job in _jobs.items()
                     if account == owner and job.status in ACTIVE_STATUSES), None)


def cancel_collection(email: str, job_id: str) -> None:
    with _lock:
        job = _jobs.get((library_owner_id(email), job_id))
        if job is None or job.status != "running":
            return
        # Send only one cancellation: repeated cancellation could interrupt the
        # browser/context manager while it is releasing the profile lease.
        job.status = "cancelling"
        job.cancel.set()
        if job.loop is not None and job.task is not None:
            try:
                job.loop.call_soon_threadsafe(job.task.cancel)
            except RuntimeError:
                pass  # The worker has already completed and closed its loop.


def start_collection(email: str, urls: list[str], run: Callable[..., Awaitable[list[dict[str, Any]]]]) -> str:
    owner = library_owner_id(email)
    with _lock:
        for key, job in list(_jobs.items()):
            if job.finished_at and time.time() - job.finished_at > 600:
                _jobs.pop(key)
            elif key[0] == owner and job.status in ACTIVE_STATUSES:
                return job.id
        job = CollectionJob(uuid4().hex, tuple(urls))
        _jobs[(owner, job.id)] = job

    def progress(done: int, total: int, url: str) -> None:
        job.completed = done

    def partial(rows: list[dict[str, Any]]) -> None:
        job.results = list(rows)

    async def execute() -> None:
        with _lock:
            job.loop = asyncio.get_running_loop()
            job.task = asyncio.current_task()
        if job.cancel.is_set():
            raise asyncio.CancelledError()
        job.results = await run(progress, partial)

    def worker() -> None:
        status = "complete"
        try:
            asyncio.run(execute())
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:
            logging.getLogger(__name__).error("Collection worker failed (%s)", type(exc).__name__)
            job.error = "Collection stopped unexpectedly. Any completed results have been kept. Please try again."
            status = "error"
        finally:
            with _lock:
                job.loop = job.task = None
                job.finished_at = time.time()
                job.status = status

    threading.Thread(target=worker, name="facebook-collection", daemon=True).start()
    return job.id
