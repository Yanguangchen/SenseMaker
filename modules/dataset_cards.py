"""Shared source-colored dataset cards for browsing and analysis selection."""

from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from modules.analysis_library import SOURCE_LABELS

THEME_DIR = Path(__file__).parent / "theme"
SOURCE_TONES = {label: key.removeprefix("source:") for key, label in SOURCE_LABELS.items()}


def viewer_timezone():
    try:
        return ZoneInfo(st.context.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def featured_datasets(groups: list[dict], now: datetime) -> tuple[bool, list[dict]]:
    """Show every dataset saved today in the viewer's timezone, or the six latest."""
    today = [group for group in groups if group.get("timestamp")
             and datetime.fromisoformat(group["timestamp"]).astimezone(now.tzinfo).date() == now.date()]
    return bool(today), today or groups[:6]


def local_dataset_date(group: dict, local_tz, *, seconds: bool = False) -> str:
    if not group.get("timestamp"):
        return "Date unavailable"
    return datetime.fromisoformat(group["timestamp"]).astimezone(local_tz).strftime(
        "%d %b %Y, %H:%M:%S %Z" if seconds else "%d %b %Y, %H:%M %Z"
    )


def apply_dataset_card_theme() -> None:
    st.html(THEME_DIR / "source-colors.css")
    st.html(THEME_DIR / "dataset-cards.css")


def render_dataset_cards(
    groups: list[dict], section: str, local_tz, *, key_prefix: str,
    on_select: Callable[[str], None], selected: list[str] | None = None,
) -> None:
    """Open a dataset, or toggle inclusion when a selection list is supplied."""
    with st.container(key=f"sm_dataset_grid_{key_prefix}_{section}"):
        for group in groups:
            sources = group.get("sources") or [SOURCE_LABELS["source:other"]]
            tone = SOURCE_TONES.get(sources[0], "other") if len(sources) == 1 else "mixed"
            card_id = sha256(group["key"].encode()).hexdigest()[:16]
            chosen = selected is not None and group["key"] in selected
            with st.container(border=True, key=f"sm_dataset_card_{tone}_{key_prefix}_{section}_{card_id}"):
                title = escape(group["title"])
                badges = "".join(
                    f'<span class="sm-dataset-source" data-sm-source="{SOURCE_TONES.get(source, "other")}">'
                    f'{escape(source)}</span>' for source in sources
                )
                mixed = ""
                if tone == "mixed":
                    stripe = "".join(f'<span data-sm-source="{SOURCE_TONES.get(source, "other")}"></span>'
                                     for source in sources)
                    mixed = (f'<div class="sm-dataset-source-strip" aria-hidden="true">{stripe}</div>'
                             '<p class="sm-dataset-mixed">Mixed sources</p>')
                state = " sm-dataset-card--selected" if chosen else ""
                st.html(f'<div class="sm-dataset-card{state}">{mixed}'
                        f'<div class="sm-dataset-sources" role="group" aria-label="Data sources">{badges}</div>'
                        f'<h4 title="{title}">{title}</h4>'
                        f'<p class="sm-dataset-count">{group["count"]:,} records '
                        f'<span>· {group["pending"]:,} pending</span></p>'
                        f'<p class="sm-dataset-date">Latest save · {escape(local_dataset_date(group, local_tz))}</p></div>')
                label = "Open dataset" if selected is None else "✓ Selected · Remove" if chosen else "Select dataset"
                action = "View records in" if selected is None else "Remove" if chosen else "Select"
                st.button(label, key=f"{key_prefix}_card_{section}_{group['key']}",
                          help=f"{action} {group['title']}", width="stretch",
                          on_click=on_select, args=(group["key"],))
