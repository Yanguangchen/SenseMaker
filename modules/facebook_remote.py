"""Interactive hosted Facebook login with private, durable Browserbase profiles.

Only profile IDs and display metadata reach Firestore. Browser cookies stay in
Browserbase; API keys and CDP connection URLs never cross into the frontend.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from browserbase import AsyncBrowserbase, Browserbase
from firebase_admin import firestore
from google.api_core.exceptions import AlreadyExists

from modules.config import get_setting
from modules.database import init_db
from modules.facebook_session import FacebookLoginError, FacebookSession, _profile_session
from modules.saved_links import library_owner_id

LOGIN_SECONDS = 180
_jobs: dict[tuple[str, str], "LoginJob"] = {}
_jobs_lock = threading.Lock()


def remote_facebook_configured() -> bool:
    return all(get_setting(key) for key in ("BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"))


def _profile_ref(email: str):
    return init_db().collection("sensemaker_facebook_profiles").document(library_owner_id(email))


def _record(email: str) -> dict:
    return _profile_ref(email).get(timeout=15).to_dict() or {}


def remote_facebook_session(email: str) -> FacebookSession:
    if not remote_facebook_configured():
        return FacebookSession()
    record = _record(email)
    if not record.get("context_id") or not record.get("user_id") or record.get("needs_login"):
        return FacebookSession()
    recent = 0 <= time.time() - record.get("verified_at", 0) < 300
    return FacebookSession("connected" if recent else "saved", record["user_id"], record.get("name", ""))


def _ensure_profile(email: str) -> str:
    ref = _profile_ref(email)
    record = ref.get(timeout=15).to_dict() or {}
    if record.get("context_id"):
        return record["context_id"]
    with Browserbase(api_key=get_setting("BROWSERBASE_API_KEY"), max_retries=0, timeout=20) as bb:
        context = bb.contexts.create(project_id=get_setting("BROWSERBASE_PROJECT_ID"))
        try:
            ref.create({"context_id": context.id}, timeout=15)
        except AlreadyExists:
            bb.contexts.delete(context.id)
            return _record(email)["context_id"]
        except Exception:
            bb.contexts.delete(context.id)
            raise
    return context.id


def _acquire(email: str, seconds: int) -> tuple[str, str]:
    """Serialize use of each profile across users' tabs and hosted containers."""
    context_id = _ensure_profile(email)
    ref = _profile_ref(email)
    lease = uuid4().hex

    @firestore.transactional
    def acquire(transaction):
        record = ref.get(transaction=transaction).to_dict() or {}
        if record.get("busy_until", 0) > time.time():
            raise FacebookLoginError("Facebook is already open in another login or collection. "
                                     "Finish that task, or wait a few minutes and try again.")
        transaction.update(ref, {"lease": lease, "busy_until": time.time() + seconds + 30})

    acquire(init_db().transaction())
    return context_id, lease


def _release(email: str, lease: str, identity: FacebookSession | None) -> None:
    ref = _profile_ref(email)

    @firestore.transactional
    def release(transaction):
        record = ref.get(transaction=transaction).to_dict() or {}
        if record.get("lease") != lease:
            return
        updates: dict[str, Any] = {"lease": "", "busy_until": 0}
        if identity and identity.status == "connected":
            updates.update(user_id=identity.user_id, name=identity.name,
                           verified_at=time.time(), needs_login=False)
        elif identity and identity.status == "disconnected":
            updates["needs_login"] = True
        transaction.update(ref, updates)

    release(init_db().transaction())


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, FacebookLoginError):
        return str(exc)
    if getattr(exc, "status_code", 0) in {402, 429}:
        return "The remote browser usage limit has been reached. Please try again later or check your Browserbase plan."
    logging.getLogger(__name__).error("Remote Facebook browser failed (%s)", type(exc).__name__)
    return "Facebook’s remote browser could not connect. Please try again."


@asynccontextmanager
async def remote_facebook_context(playwright, email: str, *, timeout_seconds: int = 240,
                                  on_ready=None, verify_on_exit: bool = True, check_before_use: bool = False):
    """Reuse the owner's complete browser profile and persist it before unlocking."""
    if not remote_facebook_configured():
        raise FacebookLoginError("Production Facebook login is not configured yet.")
    lease = ""
    identity = None
    browser = None
    session = None
    try:
        context_id, lease = await asyncio.to_thread(_acquire, email, timeout_seconds)
        async with AsyncBrowserbase(api_key=get_setting("BROWSERBASE_API_KEY"), max_retries=0, timeout=20) as bb:
            try:
                session = await bb.sessions.create(
                    project_id=get_setting("BROWSERBASE_PROJECT_ID"), api_timeout=timeout_seconds + 15,
                    browser_settings={"context": {"id": context_id, "persist": True},
                                      "record_session": False, "log_session": False, "solve_captchas": False},
                )
                browser = await playwright.chromium.connect_over_cdp(session.connect_url, timeout=30_000)
                context = browser.contexts[0]
                if check_before_use:
                    page = await context.new_page()
                    identity = await _profile_session(page, context)
                    if identity.status != "connected":
                        raise FacebookLoginError("Your saved Facebook login needs to be renewed. Click Log in to Facebook.")
                if on_ready:
                    links = await bb.sessions.debug(session.id, expires_in=timeout_seconds)
                    url = links.debugger_fullscreen_url
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or parsed.hostname not in {"www.browserbase.com", "browserbase.com"}:
                        raise FacebookLoginError("The remote login window could not be opened. Please try again.")
                    on_ready(url)
                yield context
                if verify_on_exit:
                    page = await context.new_page()
                    identity = await _profile_session(page, context)
                    if identity.status != "connected":
                        raise FacebookLoginError("Facebook could not confirm your login. Reconnect and complete any Facebook verification.")
            finally:
                try:
                    if browser:
                        await browser.close()
                finally:
                    if session:
                        # Also terminate when connecting failed or the viewer was abandoned.
                        try:
                            await bb.sessions.update(session.id, project_id=get_setting("BROWSERBASE_PROJECT_ID"),
                                                     status="REQUEST_RELEASE")
                        except Exception:
                            pass
                        # Browserbase flushes profile changes asynchronously after close.
                        await asyncio.sleep(3)
    except Exception as exc:
        raise FacebookLoginError(_safe_error(exc)) from None
    finally:
        if lease:
            await asyncio.to_thread(_release, email, lease, identity)


@dataclass
class LoginJob:
    status: str = "starting"
    live_url: str = ""
    error: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    finished_at: float = 0


def login_job(email: str, job_id: str) -> LoginJob | None:
    with _jobs_lock:
        return _jobs.get((library_owner_id(email), job_id))


def forget_login(email: str, job_id: str) -> None:
    with _jobs_lock:
        _jobs.pop((library_owner_id(email), job_id), None)


async def _login(email: str, job: LoginJob, check_only: bool) -> None:
    from playwright.async_api import Error as BrowserError, async_playwright

    async with async_playwright() as p:
        async with remote_facebook_context(p, email, on_ready=lambda url: setattr(job, "live_url", url),
                                           verify_on_exit=True) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            if check_only:
                identity = await _profile_session(page, context)
                if identity.status != "connected":
                    # Expired profiles need an interactive login next time.
                    _profile_ref(email).update({"needs_login": True}, timeout=15)
                    raise FacebookLoginError("Your saved Facebook login needs to be renewed. Click Log in to Facebook.")
            else:
                try:
                    await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=30_000)
                except BrowserError:
                    pass  # Let the user finish a slow page load in the live view.
                job.status = "waiting"
                deadline = time.monotonic() + LOGIN_SECONDS
                while time.monotonic() < deadline and not job.cancel.is_set():
                    try:
                        identity = await _profile_session(page, context, navigate=False)
                        if identity.status == "connected":
                            break
                    except BrowserError:
                        pass  # Login redirects replace the page's execution context.
                    await asyncio.sleep(1)
                else:
                    raise FacebookLoginError("Facebook login was cancelled or timed out. Click Log in to Facebook to try again.")
            job.status = "saving"


def start_login(email: str, *, check_only: bool = False) -> str:
    owner = library_owner_id(email)
    with _jobs_lock:
        for key, value in list(_jobs.items()):
            if value.finished_at and time.time() - value.finished_at > 600:
                _jobs.pop(key)
            elif key[0] == owner and value.status not in {"complete", "error"}:
                return key[1]
        job_id, job = uuid4().hex, LoginJob()
        _jobs[(owner, job_id)] = job

    def run():
        try:
            asyncio.run(_login(email, job, check_only))
            job.status = "complete"
        except Exception as exc:
            job.error = _safe_error(exc)
            job.status = "error"
        finally:
            job.finished_at = time.time()

    threading.Thread(target=run, name="facebook-login", daemon=True).start()
    return job_id
