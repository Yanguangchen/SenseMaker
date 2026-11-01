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
from typing import Any, Callable
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
    recent = not record.get("verification_pending") and 0 <= time.time() - record.get("verified_at", 0) < 300
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
                           verified_at=time.time(), needs_login=False, verification_pending=False)
        elif identity and identity.status == "disconnected":
            updates["needs_login"] = True
        elif identity and identity.status == "unavailable":
            # Preserve the saved profile and identity; offer a connection retry.
            updates["verification_pending"] = True
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
                                  on_ready=None, verify_on_exit: bool = True, check_before_use: bool = False,
                                  confirmed_session: Callable[[], FacebookSession | None] | None = None):
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
                        logging.getLogger(__name__).warning("Facebook connection check: %s", identity.status)
                        if identity.status == "disconnected":
                            raise FacebookLoginError("Facebook is asking you to sign in or complete verification. Click Log in to Facebook.")
                        raise FacebookLoginError("Facebook's sign-in check did not finish loading. Your saved login has been kept. "
                                                 "Click Check Facebook connection to try again.")
                if on_ready:
                    links = await bb.sessions.debug(session.id, expires_in=timeout_seconds)
                    url = links.debugger_fullscreen_url
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or parsed.hostname not in {"www.browserbase.com", "browserbase.com"}:
                        raise FacebookLoginError("The remote login window could not be opened. Please try again.")
                    on_ready(url)
                yield context
                if confirmed_session is not None:
                    # Interactive login verifies the visible Facebook page after
                    # the user finishes. Do not navigate a second tab and close
                    # the viewer because that fresh page is still loading.
                    identity = confirmed_session()
                    if identity is None or identity.status != "connected":
                        raise FacebookLoginError("Facebook login has not been confirmed yet.")
                elif verify_on_exit:
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
    notice: str = ""
    finish: threading.Event = field(default_factory=threading.Event)
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
        if check_only:
            job.status = "checking"
            # A saved-session check has no interactive viewer to open or close.
            async with remote_facebook_context(p, email, check_before_use=True, verify_on_exit=False):
                job.status = "saving"
            return
        confirmed = None
        async with remote_facebook_context(p, email, on_ready=lambda url: setattr(job, "live_url", url),
                                           verify_on_exit=False, confirmed_session=lambda: confirmed) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=30_000)
            except BrowserError:
                pass  # Let the user finish a slow page load in the live view.
            job.status = "waiting"
            deadline = time.monotonic() + LOGIN_SECONDS
            while time.monotonic() < deadline:
                if job.cancel.is_set():
                    raise FacebookLoginError("Facebook login was cancelled. Open the login window to try again.")
                if not context.browser.is_connected():
                    raise FacebookLoginError("The remote Facebook login window disconnected. Open it again to continue.")
                if page.is_closed():
                    pages = [candidate for candidate in context.pages if not candidate.is_closed()]
                    if not pages:
                        raise FacebookLoginError("The Facebook login window was closed. Open it again to continue.")
                    page = pages[-1]
                if job.finish.is_set():
                    job.finish.clear()
                    job.status = "checking"
                    job.notice = ""
                    try:
                        identity = await _profile_session(page, context, navigate=False)
                        if identity.status == "connected":
                            # Recheck after redirects/account switches settle. A
                            # cookie alone never counts as an authenticated page.
                            await asyncio.sleep(1)
                            stable = await _profile_session(page, context, navigate=False)
                            if stable.status == "connected" and stable.user_id == identity.user_id:
                                if job.cancel.is_set():
                                    raise FacebookLoginError("Facebook login was cancelled. Open the login window to try again.")
                                confirmed = stable
                                job.status = "saving"
                                job.live_url = ""
                                break
                    except BrowserError:
                        pass  # Keep the live view open while a redirect finishes.
                    job.notice = ("Facebook has not confirmed sign-in yet. Finish any verification in the window, "
                                  "then click Save Facebook login again.")
                    job.status = "waiting"
                await asyncio.sleep(0.5)
            else:
                raise FacebookLoginError("The Facebook login window expired after three minutes. "
                                         "Open it again, finish verification, and click Save Facebook login.")


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
            logging.getLogger(__name__).warning("Facebook login job ended with an error (%s)", type(exc).__name__)
        finally:
            job.live_url = ""
            job.finished_at = time.time()

    threading.Thread(target=run, name="facebook-login", daemon=True).start()
    return job_id
