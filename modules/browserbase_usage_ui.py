"""A compact, automatically refreshed usage bar above the dashboard workspaces."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from modules.browserbase_usage import BrowserbaseUsage, browser_allowance, load_browserbase_usage
from modules.config import get_setting


def _usage_markup(usage: BrowserbaseUsage, allowance_label: str, limit: float | None, *, stale: bool) -> str:
    used = usage.browser_minutes
    tone = "normal"
    meter = ""
    if limit is None:
        summary = f"<b>{used:,.0f}</b> browser min used"
        remaining = "Confirm your allowance in Browserbase"
    else:
        ratio = used / limit
        tone = "full" if ratio >= 1 else "near" if ratio >= 0.8 else "normal"
        summary = f"<b>{used:,.0f}</b> / {limit:,.0f} browser min"
        remaining = (f"{used - limit:,.0f} min over allowance" if used > limit else f"{limit - used:,.0f} min remaining")
        remaining += f" · {ratio:.0%} used"
        percent = min(100, max(0, ratio * 100))
        meter = (f'<div class="sm-bb-meter" role="progressbar" aria-label="Browserbase browser time used" '
                 f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{percent:.2f}" '
                 f'aria-valuetext="{used:g} of {limit:g} browser minutes used">'
                 f'<span style="width:{percent:.2f}%"></span></div>')
    checked = datetime.fromtimestamp(usage.checked_at, timezone.utc).strftime("%H:%M UTC")
    meta = f'<div class="sm-bb-meta">Last successful update {checked} · Refresh unavailable</div>' if stale else ""
    return (
        f'<div class="sm-bb sm-bb--{tone}">'
        '<div class="sm-bb-heading"><strong>Browserbase</strong></div>'
        f'<div class="sm-bb-summary"><span>{summary}</span><span>{remaining}</span></div>{meter}'
        f'{meta}</div>'
    )


@st.fragment(run_every=60)
def render_browserbase_usage_bar(user_email: str) -> None:
    # Timed refreshes bypass main(), so recheck access before reading account usage.
    if not st.session_state.get("auth_token"):
        st.session_state.pop("browserbase_usage_snapshot", None)
        return
    from modules.auth import allowed_emails, verify_access
    try:
        if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != user_email:
            raise PermissionError()
    except Exception:
        st.session_state.pop("browserbase_usage_snapshot", None)
        st.rerun(scope="app")

    st.html(Path(__file__).parent / "theme" / "browserbase-usage.css")
    api_key, project_id = get_setting("BROWSERBASE_API_KEY"), get_setting("BROWSERBASE_PROJECT_ID")
    identity = hashlib.sha256(f"{api_key}:{project_id}".encode()).hexdigest()
    with st.container(border=True, key="browserbase_usage_bar"):
        content, actions = st.columns([9, 1], vertical_alignment="center")
        with actions:
            refresh = st.button("↻", key="refresh_browserbase_usage", help="Refresh Browserbase usage",
                                disabled=not api_key, width="stretch")
        if not api_key:
            content.markdown("**Browserbase** · Not configured")
            return
        if refresh:
            load_browserbase_usage.clear(api_key, project_id)
        usage = load_browserbase_usage(api_key, project_id)
        previous = st.session_state.get("browserbase_usage_snapshot")
        stale = bool(usage.error)
        if not usage.error:
            st.session_state["browserbase_usage_snapshot"] = (identity, usage)
        elif previous and previous[0] == identity:
            usage = previous[1]
        else:
            st.session_state.pop("browserbase_usage_snapshot", None)
            content.markdown(f"**Browserbase** · {usage.error}")
            return
        label, limit = browser_allowance()
        content.markdown(_usage_markup(usage, label, limit, stale=stale), unsafe_allow_html=True)
