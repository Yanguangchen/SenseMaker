"""Inspect and capture the Facebook browser session used by the scraper."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from modules.config import get_setting

APP_DIR = Path(__file__).resolve().parents[1]
_STATE_LOCK = threading.Lock()


class FacebookLoginError(RuntimeError):
    """A login failure that can be displayed without exposing browser internals."""


@dataclass(frozen=True)
class FacebookSession:
    status: str = "disconnected"
    user_id: str = ""
    name: str = ""


def facebook_storage_path() -> str:
    """Use the same durable file for login, dashboard checks and collection."""
    path = Path(get_setting("PLAYWRIGHT_STORAGE_STATE") or "storage_state.json").expanduser()
    return str((path if path.is_absolute() else APP_DIR / path).resolve())


def _read_state(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
            return {}
        if not all(isinstance(cookie, dict) for cookie in data["cookies"]):
            return {}
        if not isinstance(data.get("sensemaker"), dict):
            data["sensemaker"] = {}
        return data
    except (OSError, ValueError):
        return {}


def facebook_state_revision(path: str) -> tuple[int, int] | None:
    try:
        info = Path(path).stat()
        return info.st_mtime_ns, info.st_size
    except OSError:
        return None


def _write_state(path: str, state: dict[str, Any]) -> None:
    """Replace atomically so parallel collectors never read a partial cookie file."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".storage_state-", suffix=".json", delete=False) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            json.dump(state, handle)
        os.replace(temporary, output)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def facebook_context_options(path: str) -> dict[str, Any]:
    """Restore cookies, local storage and IndexedDB with the original browser identity."""
    data = _read_state(path)
    if not data:
        return {}
    options: dict[str, Any] = {
        "storage_state": {"cookies": data["cookies"], "origins": data.get("origins", [])},
    }
    user_agent = data.get("sensemaker", {}).get("user_agent")
    if isinstance(user_agent, str) and user_agent:
        options["user_agent"] = user_agent
    return options


def saved_facebook_session(path: str, fresh_for: int = 300) -> FacebookSession:
    data = _read_state(path)
    user_id = facebook_user_id(data.get("cookies", []))
    metadata = data.get("sensemaker", {})
    if not user_id or metadata.get("needs_login"):
        return FacebookSession()
    name = str(metadata.get("name", "")) if metadata.get("user_id") == user_id else ""
    try:
        age = time.time() - float(metadata.get("verified_at", 0))
    except (TypeError, ValueError):
        age = float("inf")
    confirmed = metadata.get("user_id") == user_id and 0 <= age < fresh_for
    return FacebookSession("connected" if confirmed else "saved", user_id, name)


async def save_facebook_session(
    context: Any, path: str, expected_revision: tuple[int, int] | None, *,
    session: FacebookSession | None = None, user_agent: str = "",
) -> bool:
    """Keep renewed auth state without letting an older worker overwrite a newer login."""
    state = await context.storage_state(indexed_db=True)
    user_id = facebook_user_id(state.get("cookies", []))
    if not user_id:
        return False
    if session is not None and (session.status != "connected" or session.user_id != user_id):
        return False
    with _STATE_LOCK:
        if facebook_state_revision(path) != expected_revision:
            return False
        previous = _read_state(path)
        previous_id = facebook_user_id(previous.get("cookies", []))
        if session is None and previous_id != user_id:
            return False
        metadata = dict(previous.get("sensemaker", {})) if previous_id == user_id else {}
        if user_agent:
            metadata["user_agent"] = user_agent
        if session is not None and session.status == "connected" and session.user_id == user_id:
            metadata.update(user_id=user_id, name=session.name, verified_at=time.time(), needs_login=False)
        state["sensemaker"] = metadata
        _write_state(path, state)
    return True


def _mark_login_required(path: str, revision: tuple[int, int] | None) -> None:
    with _STATE_LOCK:
        if facebook_state_revision(path) != revision:
            return
        state = _read_state(path)
        if state:
            state.setdefault("sensemaker", {})["needs_login"] = True
            _write_state(path, state)


async def launch_facebook_browser(playwright: Any, *, headless: bool) -> Any:
    try:
        return await playwright.chromium.launch(channel="chrome", headless=headless)
    except Exception as exc:
        if not any(message in str(exc).lower() for message in ("not found", "executable doesn't exist")):
            raise
        return await playwright.chromium.launch(headless=headless)


def facebook_user_id(cookies: list[dict[str, Any]]) -> str:
    """Ignore expired cookies and cookies outside Facebook's domain."""
    active = {}
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lstrip(".").lower()
        if domain != "facebook.com" and not domain.endswith(".facebook.com"):
            continue
        try:
            expires = float(cookie.get("expires", -1))
        except (TypeError, ValueError):
            continue
        if expires != -1 and expires <= time.time():
            continue
        active[cookie.get("name")] = cookie.get("value", "")
    user_id = str(active.get("c_user", ""))
    return user_id if user_id.isdigit() and active.get("xs") else ""


def saved_facebook_user_id(path: str) -> str:
    return facebook_user_id(_read_state(path).get("cookies", []))


async def _facebook_page_session(page: Any, context: Any) -> FacebookSession:
    """Inspect authentication without moving away from the current page."""
    location = urlparse(page.url)
    host = (location.hostname or "").lower()
    if (
        any(part in location.path.lower() for part in ("login", "checkpoint", "recover", "two_step"))
        or await page.locator('input[type="password"]:visible').count()
    ):
        return FacebookSession()
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        return FacebookSession(status="unavailable")
    user_id = facebook_user_id(await context.cookies())
    if not user_id:
        return FacebookSession(status="unavailable")
    # Use the same authenticated account controls for login and collection.
    # A profile heading/title is neither required nor proof of authentication:
    # Facebook can leave /me loading, or render an unrelated heading such as Chats.
    account = page.locator('[aria-label="Account controls and settings"]:visible, '
                           '[aria-label="Account, your profile"]:visible')
    if not await account.count():
        return FacebookSession(status="unavailable")
    profile = page.locator('a[aria-label$="\'s timeline"]').first
    label = await profile.get_attribute("aria-label") if await profile.count() else ""
    name = (label or "").removesuffix("'s timeline").strip()
    return FacebookSession(status="connected", user_id=user_id, name=name[:160])


async def _profile_session(page: Any, context: Any, *, navigate: bool = True) -> FacebookSession:
    from playwright.async_api import Error as BrowserError

    if navigate:
        try:
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=20_000)
        except BrowserError:
            pass  # A navigation timeout can still leave a usable page to inspect.
    # DOMContentLoaded precedes Facebook's authenticated UI. Allow hydration and
    # redirects to finish, without repeatedly navigating or declaring logout.
    attempts = 13 if navigate else 1
    for attempt in range(attempts):
        try:
            session = await _facebook_page_session(page, context)
        except BrowserError:
            session = FacebookSession(status="unavailable")
        if session.status != "unavailable" or attempt == attempts - 1:
            return session
        await asyncio.sleep(1)


def _run(coroutine: Any) -> Any:
    if sys.platform.startswith("win") and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(coroutine)


def check_facebook_session(path: str) -> FacebookSession:
    if not saved_facebook_user_id(path):
        return FacebookSession()

    async def check() -> FacebookSession:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await launch_facebook_browser(p, headless=True)
            try:
                revision = facebook_state_revision(path)
                context = await browser.new_context(**facebook_context_options(path))
                page = await context.new_page()
                session = await _profile_session(page, context)
                if session.status == "connected":
                    await save_facebook_session(context, path, revision, session=session,
                                                user_agent=await page.evaluate("navigator.userAgent"))
                elif session.status == "disconnected":
                    _mark_login_required(path, revision)
                else:
                    return saved_facebook_session(path, fresh_for=0)
                return session
            finally:
                await browser.close()

    try:
        return _run(check())
    except Exception:
        # A failed network/profile check does not mean the stored login expired.
        return saved_facebook_session(path, fresh_for=0)


def capture_facebook_session(path: str, timeout_seconds: int = 240) -> FacebookSession:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    async def capture() -> FacebookSession:
        from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeout, async_playwright

        async with async_playwright() as p:
            browser = await launch_facebook_browser(p, headless=False)
            try:
                revision = facebook_state_revision(str(output))
                context = await browser.new_context(**facebook_context_options(str(output)))
                page = await context.new_page()
                await page.bring_to_front()
                try:
                    await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
                except BrowserTimeout:
                    # A slow initial load can still finish while the user signs in.
                    pass
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    if page.is_closed() or not browser.is_connected():
                        raise FacebookLoginError("The Facebook window closed before your login was saved. "
                                                 "Click Log in to Facebook and keep that window open until CrisisRadar connects.")
                    try:
                        session = await _profile_session(page, context, navigate=False)
                        if session.status != "connected":
                            await asyncio.sleep(1)
                            continue
                        saved = await save_facebook_session(
                            context, str(output), revision, session=session,
                            user_agent=await page.evaluate("navigator.userAgent"),
                        )
                        if not saved:
                            raise FacebookLoginError("The saved Facebook session changed in another window. "
                                                     "Click Log in to Facebook again to reconnect.")
                        return session
                    except BrowserError as exc:
                        # Redirects replace the page's JavaScript context. They are
                        # expected during login and must not close the login window.
                        if page.is_closed() or not browser.is_connected():
                            raise FacebookLoginError("The Facebook window closed before your login was saved. "
                                                     "Keep it open until CrisisRadar connects.") from None
                        if not isinstance(exc, BrowserTimeout) and not any(
                            message in str(exc).lower() for message in (
                                "execution context was destroyed", "cannot find context",
                                "interrupted by another navigation",
                            )
                        ):
                            raise
                        await asyncio.sleep(1)
                raise FacebookLoginError("Facebook sign-in timed out. Click Log in to Facebook again, "
                                         "then finish signing in and any verification in the window that opens.")
            finally:
                await browser.close()

    return _run(capture())
