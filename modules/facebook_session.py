"""Inspect and capture the Facebook browser session used by the scraper."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class FacebookSession:
    status: str = "disconnected"
    user_id: str = ""
    name: str = ""


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
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cookies = data.get("cookies", []) if isinstance(data, dict) else []
        if not isinstance(cookies, list) or not all(isinstance(c, dict) for c in cookies):
            return ""
        return facebook_user_id(cookies)
    except (OSError, ValueError):
        return ""


async def _profile_session(page: Any, context: Any) -> FacebookSession:
    await page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=20_000)
    location = urlparse(page.url)
    host = (location.hostname or "").lower()
    if (
        (host != "facebook.com" and not host.endswith(".facebook.com"))
        or any(part in location.path.lower() for part in ("login", "checkpoint", "recover", "two_step"))
        or await page.locator('input[type="password"]:visible').count()
    ):
        return FacebookSession()
    user_id = facebook_user_id(await context.cookies())
    if not user_id:
        return FacebookSession()
    try:
        name = (await page.locator("h1").first.inner_text(timeout=5_000)).strip()
    except Exception:
        name = ""
    if not name:
        title = await page.title()
        name = re.sub(r"\s*[|–-]\s*Facebook\s*$", "", title, flags=re.I)
        name = re.sub(r"^\(\d+\)\s*", "", name).strip()
        if name.lower() in ("facebook", "log in to facebook", ""):
            # A cookie alone cannot confirm that Facebook accepted the session.
            return FacebookSession(status="unavailable")
    return FacebookSession(status="connected", user_id=user_id, name=name[:160])


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
            browser = await p.chromium.launch(channel="chrome", headless=True)
            try:
                context = await browser.new_context(storage_state=path)
                return await _profile_session(await context.new_page(), context)
            finally:
                await browser.close()

    try:
        return _run(check())
    except Exception:
        return FacebookSession(status="unavailable")


def capture_facebook_session(path: str, timeout_seconds: int = 240) -> FacebookSession:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    async def capture() -> FacebookSession:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome", headless=False)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    if facebook_user_id(await context.cookies()):
                        session = await _profile_session(page, context)
                        if session.status != "connected":
                            raise RuntimeError("Finish signing in to Facebook, then try again.")
                        await context.storage_state(path=str(output))
                        output.chmod(0o600)
                        return session
                    await page.wait_for_timeout(1000)
                raise RuntimeError("Facebook sign-in timed out. Please try again.")
            finally:
                await browser.close()

    return _run(capture())
