"""A persistent glass navigation rail backed by Streamlit's tab state.

Keeping the views mounted preserves form inputs and collected/loaded records when
switching workspaces. The standard tabs remain usable if the enhancement cannot load.
"""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from modules.facebook_session import FacebookSession

THEME_DIR = Path(__file__).parent / "theme"


def _icon(paths: str) -> str:
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


def dashboard_views(email: str, facebook_session: FacebookSession | None = None):
    """Render navigation only after require_access has verified the account."""
    panel = _icon('<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>')
    chart = _icon('<path d="M4 4v16h16M8 14l4-5 4 3 4-7"/>')
    trend = _icon('<path d="m3 17 6-6 4 4 8-12M15 3h6v6"/>')
    speed = _icon('<path d="M4 18a9 9 0 1 1 16 0M12 12l5-5M5 12H3M12 5V3M19 12h2"/><circle cx="12" cy="12" r="2"/>')
    content = _icon('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5"/>')
    voice = _icon('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M9 10h6M9 14h4"/>')
    guide = _icon('<path d="M12 6c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1zM12 6v15"/>')
    records = _icon('<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M9 9v12M13 13h4M13 17h4"/>')
    collect = _icon('<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3M4 12v6c0 1.7 3.6 3 8 3M20 5v5M16 16l3 3 3-3M19 12v7"/>')
    logout = _icon('<path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M10 12h11M17 8l4 4-4 4"/>')
    chevron = _icon('<path d="m14 8-4 4 4 4"/>')
    safe_email = html.escape(email, quote=True)
    facebook_account = ""
    if facebook_session is not None and facebook_session.status in {"connected", "saved"}:
        connection = "Connected" if facebook_session.status == "connected" else "Saved"
        name = " ".join((facebook_session.name or f"Facebook account {facebook_session.user_id}").split())
        safe_name = html.escape(name, quote=True)
        facebook_account = (
            f'<div class="sm-rail-account sm-rail-facebook" title="Facebook {connection.lower()}: {safe_name}" '
            f'role="img" aria-label="Facebook {connection.lower()}: {safe_name}">'
            '<span class="sm-rail-avatar sm-rail-facebook-avatar" aria-hidden="true">f</span>'
            f'<span class="sm-rail-label sm-rail-account-meta"><span>Facebook · {connection}</span>'
            f'<span class="sm-rail-email">{safe_name}</span></span></div>'
        )
    with st.sidebar.container(key="sm_navigation"):
        st.markdown(
            '<div class="sm-sidebar">'
            '<button class="sm-rail-button sm-sidebar-toggle" type="button" '
            'aria-label="Collapse sidebar" aria-expanded="true" aria-controls="sm-workspace-nav" '
            'title="Collapse sidebar">'
            f'<span class="sm-rail-icon">{panel}</span>'
            '<span class="sm-rail-label sm-brand">CrisisRadar</span>'
            f'<span class="sm-sidebar-chevron">{chevron}</span></button>'
            '<nav id="sm-workspace-nav" class="sm-workspace-nav" aria-label="Workspaces">'
            '<button class="sm-rail-button" type="button" data-sm-view="Scrap data" '
            'aria-label="Scrape Data" title="Scrape Data">'
            f'<span class="sm-rail-icon">{collect}</span>'
            '<span class="sm-rail-label">Scrape Data</span></button>'
            '<button class="sm-rail-button" type="button" data-sm-view="Clean & manage data" '
            'aria-label="Clean & manage data" title="Clean & manage data">'
            f'<span class="sm-rail-icon">{records}</span>'
            '<span class="sm-rail-label">Clean & manage data</span></button>'
            '<button class="sm-rail-button" type="button" data-sm-view="Sentiment Analysis" '
            'aria-label="Sentiment Analysis" title="Sentiment Analysis">'
            f'<span class="sm-rail-icon">{chart}</span>'
            '<span class="sm-rail-label">Sentiment Analysis</span></button>'
            '<button class="sm-rail-button" type="button" data-sm-view="Velocity Tracking" '
            'aria-label="Velocity Tracking" title="Velocity Tracking">'
            f'<span class="sm-rail-icon">{speed}</span>'
            '<span class="sm-rail-label">Velocity Tracking</span></button>'
            '<button class="sm-rail-button" type="button" data-sm-view="Sentiment Trends" '
            'aria-label="Sentiment Trends" title="Sentiment Trends">'
            f'<span class="sm-rail-icon">{trend}</span>'
            '<span class="sm-rail-label">Sentiment Trends</span></button>'
            '<button class="sm-rail-button" type="button" data-sm-view="Content recommendations" '
            'aria-label="Content recommendations" title="Content recommendations">'
            f'<span class="sm-rail-icon">{content}</span>'
            '<span class="sm-rail-label">Content plan</span></button>'
            '<button class="sm-rail-button" type="button" data-sm-view="GenCite" '
            'aria-label="GenCite: AI share of voice" title="GenCite: AI share of voice">'
            f'<span class="sm-rail-icon">{voice}</span>'
            '<span class="sm-rail-label">GenCite</span></button>'
            '<button class="sm-rail-button" type="button" data-sm-view="Tutorial" '
            'aria-label="Tutorial" title="Tutorial">'
            f'<span class="sm-rail-icon">{guide}</span>'
            '<span class="sm-rail-label">Tutorial</span></button></nav>'
            '<div class="sm-sidebar-footer">'
            f'{facebook_account}'
            f'<div class="sm-rail-account" title="Signed in as {safe_email}">'
            f'<span class="sm-rail-avatar" aria-hidden="true">{html.escape(email[:1].upper())}</span>'
            '<span class="sm-rail-label sm-rail-account-meta"><span>Signed in</span>'
            f'<span class="sm-rail-email">{safe_email}</span></span></div>'
            '<button class="sm-rail-button sm-rail-sign-out" type="button" '
            'aria-label="Sign out" title="Sign out">'
            f'<span class="sm-rail-icon">{logout}</span>'
            '<span class="sm-rail-label">Sign out</span></button></div></div>',
            unsafe_allow_html=True,
        )

    st.html(THEME_DIR / "sidebar.css")
    with st.container(key="sm_views"):
        views = st.tabs(["Scrap data", "Clean & manage data", "Sentiment Analysis", "Velocity Tracking", "Sentiment Trends", "Content recommendations", "GenCite", "Tutorial"])

    script = (THEME_DIR / "sidebar.js").read_text(encoding="utf-8")
    version = hashlib.sha256(script.encode()).hexdigest()[:12]
    payload = json.dumps(script).replace("</", "<\\/")
    components.html(
        "<script>const doc = window.parent.document;"
        "const previous = doc.getElementById('sm-sidebar-script');"
        f"if (previous?.dataset.version !== '{version}') {{"
        "previous?.remove(); const script = doc.createElement('script');"
        f"script.id = 'sm-sidebar-script'; script.dataset.version = '{version}';"
        f"script.textContent = {payload}; doc.head.appendChild(script); }}</script>",
        height=0,
    )
    return views
