"""Read Browserbase project usage without starting a browser session."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import streamlit as st
from browserbase import Browserbase

from modules.config import get_setting

# Included browser hours, not enforced spending caps. Keep custom plans explicit.
# https://docs.browserbase.com/account/billing/plans (checked 2026-11-07)
PLAN_HOURS = {"free": 1, "developer": 100, "startup": 500}


@dataclass(frozen=True)
class BrowserbaseUsage:
    browser_minutes: float = 0
    proxy_bytes: float = 0
    concurrency: int = 0
    checked_at: float = 0
    error: str = ""


def browser_allowance() -> tuple[str, float | None]:
    """The public API reports consumption and concurrency, but not plan allowances."""
    custom = get_setting("BROWSERBASE_BROWSER_HOURS_LIMIT")
    if custom:
        try:
            hours = float(custom)
            if not math.isfinite(hours) or hours <= 0:
                raise ValueError()
        except ValueError:
            return "Allowance unavailable", None
        return "Configured allowance", hours * 60
    plan = get_setting("BROWSERBASE_PLAN", "developer").lower()
    hours = PLAN_HOURS.get(plan)
    return (f"{plan.title()} allowance", hours * 60) if hours is not None else ("Allowance unavailable", None)


@st.cache_data(ttl=60, max_entries=32, show_spinner=False)
def load_browserbase_usage(api_key: str, project_id: str) -> BrowserbaseUsage:
    """Cache by credential and project; return only safe aggregate fields to the UI."""
    if not api_key:
        return BrowserbaseUsage(error="Browserbase is not configured.")
    try:
        with Browserbase(api_key=api_key, max_retries=0, timeout=6) as bb:
            if project_id:
                project = bb.projects.retrieve(project_id)
            else:
                projects = bb.projects.list()
                if len(projects) != 1:
                    return BrowserbaseUsage(error="Choose the Browserbase project in the server settings to show usage.")
                project = projects[0]
            usage = bb.projects.usage(project.id)
            minutes, proxy_bytes, concurrency = float(usage.browser_minutes), float(usage.proxy_bytes), int(project.concurrency)
            if not all(math.isfinite(value) and value >= 0 for value in (minutes, proxy_bytes)) or concurrency < 1:
                raise ValueError("Invalid usage response")
            return BrowserbaseUsage(minutes, proxy_bytes, concurrency, time.time())
    except Exception:
        # Provider messages may include request details. Never render them or cache credentials in the result.
        return BrowserbaseUsage(error="Browserbase usage is temporarily unavailable. Retrying automatically.")
