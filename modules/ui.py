"""Glass theme, scroll reveal and shared markup for the SenseMaker UI."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

import streamlit as st
import streamlit.components.v1 as components

THEME_DIR = Path(__file__).parent / "theme"

# AI labels posts with one of these; anything else gets the neutral grey tone.
SENTIMENT_TONES = {"Anger": "anger", "Anxiety": "anxiety", "Joy": "joy", "Neutral": "neutral"}

_LOCK_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="4" y="10.5" width="16" height="10.5" rx="2.5"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/></svg>'
)


def apply_theme() -> None:
    """Inject the stylesheet and start the scroll reveal in the main page."""
    st.html(THEME_DIR / "glass.css")
    script = json.dumps((THEME_DIR / "reveal.js").read_text(encoding="utf-8")).replace("</", "<\\/")
    # Component iframes share the app's origin, so the loader can add the script to
    # the parent page once; it then outlives this iframe across reruns.
    components.html(
        "<script>const doc = window.parent.document;"
        "if (!doc.getElementById('sm-reveal')) {"
        "const s = doc.createElement('script'); s.id = 'sm-reveal';"
        f"s.textContent = {script}; doc.head.appendChild(s); }}</script>",
        height=0,
    )


def _text(value: Any) -> str:
    # Blank lines would end the HTML block and let Markdown parse scraped text.
    return html.escape(" ".join(str(value or "").split()))


def hero() -> None:
    st.markdown(
        '<div class="sm-hero sm-reveal">'
        '<span class="sm-eyebrow"><span class="sm-pulse" aria-hidden="true"></span>Project Sentinel</span>'
        '<div class="sm-hero__title" role="heading" aria-level="1">SenseMaker</div>'
        '<p class="sm-hero__lede">Collect posts from the sources you follow, then read their sentiment '
        "and risk with Gemini or ChatGPT.</p></div>",
        unsafe_allow_html=True,
    )


def sign_in_gate() -> None:
    st.markdown(
        f'<div class="sm-gate sm-reveal"><div class="sm-gate__icon">{_LOCK_ICON}</div>'
        '<span class="sm-eyebrow">Project Sentinel</span>'
        '<div class="sm-hero__title" role="heading" aria-level="1">SenseMaker</div>'
        '<p class="sm-hero__lede">Sign in with your approved Google account to continue.</p>'
        '<p class="sm-gate__hint">Use <b>Sign in with Google</b> in the sidebar.</p></div>',
        unsafe_allow_html=True,
    )


def account_badge(email: str) -> None:
    st.sidebar.markdown(
        f'<div class="sm-account"><span class="sm-account__avatar" aria-hidden="true">{_text(email[:1])}</span>'
        '<span class="sm-account__meta"><span class="sm-account__label">Signed in</span>'
        f'<span class="sm-account__email" title="{_text(email)}">{_text(email)}</span></span></div>',
        unsafe_allow_html=True,
    )


def _risk(score: Any) -> str:
    try:
        value = max(0, min(10, int(score)))
    except (TypeError, ValueError):
        return '<span class="sm-risk">Risk <b>?</b></span>'
    level = "high" if value >= 8 else "medium" if value >= 5 else "low"
    return (
        f'<span class="sm-risk sm-risk--{level}" role="img" aria-label="Risk {value} out of 10">Risk'
        f'<span class="sm-risk__bar"><i style="width: {value * 10}%"></i></span><b>{value}/10</b></span>'
    )


def _analysis_card(item: Dict[str, Any]) -> str:
    sentiment = str(item.get("sentiment") or "Unknown")
    tone = SENTIMENT_TONES.get(sentiment, "unknown")
    topics = "".join(
        f'<span class="sm-tag">{_text(topic)}</span>'
        for topic in str(item.get("topics") or "").split(",") if topic.strip()
    )
    url = str(item.get("url") or "").strip()
    link = (
        f'<a class="sm-link" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Source ↗</a>'
        if urlsplit(url).scheme in {"http", "https"} else ""
    )
    original = _text(item.get("raw_text_preview"))
    return (
        f'<article class="sm-card sm-reveal sm-tone-{tone}">'
        f'<header class="sm-card__head"><span class="sm-chip">{_text(sentiment)}</span>{_risk(item.get("risk_score"))}</header>'
        f'<p class="sm-card__text">{_text(item.get("translation")) or "No translation returned."}</p>'
        + (f'<p class="sm-card__orig">{original}</p>' if original else "")
        + f'<footer class="sm-card__foot">{topics}{link}</footer></article>'
    )


def analysis_cards(analyses: List[Dict[str, Any]]) -> None:
    """Render AI results as a responsive grid of sentiment-tinted cards."""
    cards = "".join(_analysis_card(item) for item in analyses)
    st.markdown(f'<div class="sm-grid">{cards}</div>', unsafe_allow_html=True)
