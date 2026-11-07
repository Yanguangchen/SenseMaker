"""Global dashboard alerts for high-velocity posts (>0.5M views) and high negative sentiment (>50%)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from modules.engagement import checks_for
from modules.saved_links import library_owner_id
from modules.velocity_metrics import engagement_count
from modules.velocity_tracking import card_posts

VELOCITY_VIEW_ALERT_THRESHOLD = 500_000
NEGATIVE_SENTIMENT_ALERT_THRESHOLD = 0.5


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _card_display_name(card: dict[str, Any], index: int) -> str:
    name = str(card.get("name") or "Velocity tracking card").strip()
    return f"Velocity tracking card {index}" if name == "Velocity tracking card" else name


def find_high_velocity_posts(
    cards: list[dict[str, Any]] | None = None,
    posts: list[dict[str, Any]] | None = None,
    *,
    threshold: int = VELOCITY_VIEW_ALERT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return posts whose latest recorded view count exceeds ``threshold`` (default 500,000 / 0.5M views)."""
    flagged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for card_index, card in enumerate(cards or [], 1):
        if not isinstance(card, dict):
            continue
        runs = card.get("runs")
        if not isinstance(runs, list) or not runs:
            continue
        card_name = _card_display_name(card, card_index)
        for post in card_posts(card):
            post_id = post.get("id")
            url = str(post.get("url") or "").strip()
            if not post_id or not url:
                continue
            latest_views: int | None = None
            for run in reversed(runs):
                if not isinstance(run, dict):
                    continue
                results = run.get("results")
                if not isinstance(results, list):
                    continue
                match = next((r for r in results if isinstance(r, dict) and r.get("post_id") == post_id), None)
                if match is not None:
                    views = _coerce_int(match.get("views"))
                    if views is None:
                        views = engagement_count(match)
                    if views is not None:
                        latest_views = views
                        break
            if latest_views is not None and latest_views > threshold:
                flagged.append({
                    "url": url,
                    "views": latest_views,
                    "card_name": card_name,
                })
                seen_urls.add(url)

    for post in posts or []:
        if not isinstance(post, dict):
            continue
        url = str(post.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        latest_views = _coerce_int(post.get("views"))
        for check in reversed(checks_for(post)):
            views = _coerce_int(check.get("views"))
            if views is not None:
                latest_views = views
                break
        if latest_views is not None and latest_views > threshold:
            flagged.append({
                "url": url,
                "views": latest_views,
                "card_name": None,
            })
            seen_urls.add(url)

    return flagged


def _ai_sentiment_counts(
    analysis_results: dict[str, Any] | None,
    processed_rows: list[dict[str, Any]] | None,
    sentiment_history: list[dict[str, Any]] | None,
) -> tuple[int, int] | None:
    if isinstance(analysis_results, dict):
        history_run = analysis_results.get("history_run")
        if isinstance(history_run, dict) and isinstance(history_run.get("counts"), dict):
            counts = history_run["counts"]
            negative = (_coerce_int(counts.get("Anger")) or 0) + (_coerce_int(counts.get("Anxiety")) or 0)
            total = _coerce_int(history_run.get("scored_count"))
            if total is None:
                total = sum(_coerce_int(counts.get(k)) or 0 for k in ("Joy", "Neutral", "Anger", "Anxiety"))
            if total > 0:
                return negative, total
        analyses = analysis_results.get("analyses")
        if isinstance(analyses, list) and analyses:
            sentiments = [
                str(row.get("sentiment") or (row.get("analysis") or {}).get("sentiment") or "").strip()
                for row in analyses if isinstance(row, dict)
            ]
            scored = [s for s in sentiments if s in {"Joy", "Neutral", "Anger", "Anxiety"}]
            if scored:
                negative = sum(1 for s in scored if s in {"Anger", "Anxiety"})
                return negative, len(scored)

    if isinstance(processed_rows, list) and processed_rows:
        sentiments = [
            str(row.get("sentiment") or (row.get("analysis") or {}).get("sentiment") or "").strip()
            for row in processed_rows if isinstance(row, dict)
        ]
        scored = [s for s in sentiments if s in {"Joy", "Neutral", "Anger", "Anxiety"}]
        if scored:
            negative = sum(1 for s in scored if s in {"Anger", "Anxiety"})
            return negative, len(scored)

    if isinstance(sentiment_history, list) and sentiment_history:
        for row in reversed(sentiment_history):
            if not isinstance(row, dict) or not isinstance(row.get("counts"), dict):
                continue
            counts = row["counts"]
            negative = (_coerce_int(counts.get("Anger")) or 0) + (_coerce_int(counts.get("Anxiety")) or 0)
            total = _coerce_int(row.get("scored_count"))
            if total is None:
                total = sum(_coerce_int(counts.get(k)) or 0 for k in ("Joy", "Neutral", "Anger", "Anxiety"))
            if total > 0:
                return negative, total

    return None


def _news_sentiment_counts(
    news_scan: dict[str, Any] | None,
    news_history: list[dict[str, Any]] | None,
) -> tuple[int, int] | None:
    candidates: list[dict[str, Any]] = []
    if isinstance(news_scan, dict):
        candidates.append(news_scan)
    if isinstance(news_history, list):
        candidates.extend(row for row in reversed(news_history) if isinstance(row, dict))

    for scan in candidates:
        tones = scan.get("tone_counts")
        if not isinstance(tones, dict):
            continue
        negative = _coerce_int(tones.get("Negative")) or 0
        positive = _coerce_int(tones.get("Positive")) or 0
        neutral = _coerce_int(tones.get("Neutral")) or 0
        total = positive + neutral + negative
        if total > 0:
            return negative, total
    return None


def find_negative_sentiment_flags(
    *,
    analysis_results: dict[str, Any] | None = None,
    processed_rows: list[dict[str, Any]] | None = None,
    sentiment_history: list[dict[str, Any]] | None = None,
    news_scan: dict[str, Any] | None = None,
    news_history: list[dict[str, Any]] | None = None,
    threshold: float = NEGATIVE_SENTIMENT_ALERT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return sentiment sources whose latest negative share strictly exceeds ``threshold`` (default 50%)."""
    flags: list[dict[str, Any]] = []

    ai_counts = _ai_sentiment_counts(analysis_results, processed_rows, sentiment_history)
    if ai_counts is not None:
        negative, total = ai_counts
        if total > 0 and (negative / total) > threshold:
            flags.append({
                "source": "AI Sentiment Analysis",
                "negative_count": negative,
                "total_count": total,
                "negative_pct": round(100.0 * negative / total, 1),
            })

    news_counts = _news_sentiment_counts(news_scan, news_history)
    if news_counts is not None:
        negative, total = news_counts
        if total > 0 and (negative / total) > threshold:
            flags.append({
                "source": "Online News",
                "negative_count": negative,
                "total_count": total,
                "negative_pct": round(100.0 * negative / total, 1),
            })

    return flags


def format_velocity_alert(items: list[dict[str, Any]]) -> str | None:
    if not items:
        return None
    details = []
    for item in items[:3]:
        card_suffix = f" in {item['card_name']}" if item.get("card_name") else ""
        details.append(f"{item['url']} ({item['views']:,} views{card_suffix})")
    remaining = len(items) - len(details)
    if remaining > 0:
        details.append(f"+{remaining} more")
    noun = "post" if len(items) == 1 else "posts"
    return (
        f"High velocity alert: {len(items)} {noun} exceeded 0.5M (500,000) views — "
        + "; ".join(details)
    )


def format_negative_sentiment_alert(flags: list[dict[str, Any]]) -> str | None:
    if not flags:
        return None
    parts = [
        f"{flag['source']} at {flag['negative_pct']:.1f}% negative "
        f"({flag['negative_count']} of {flag['total_count']} records)"
        for flag in flags
    ]
    return "Negative sentiment flag: Negative sentiment exceeded 50% — " + "; ".join(parts)


def _session_posts() -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    for key in ("last_scrape_results", "last_reddit_scrape_results", "firestore_rows", "processed_rows"):
        rows = st.session_state.get(key)
        if isinstance(rows, list):
            posts.extend(r for r in rows if isinstance(r, dict))
    for key in ("last_collected_dataset", "x_collection", "website_collection", "news_scan"):
        container = st.session_state.get(key)
        if isinstance(container, dict) and isinstance(container.get("rows"), list):
            posts.extend(r for r in container["rows"] if isinstance(r, dict))
    analysis_last = st.session_state.get("analysis_last_results")
    if isinstance(analysis_last, dict) and isinstance(analysis_last.get("analyses"), list):
        posts.extend(r for r in analysis_last["analyses"] if isinstance(r, dict))
    return posts


def render_global_alerts(
    user_email: str,
    *,
    sentiment_history: list[dict[str, Any]] | None = None,
    news_history: list[dict[str, Any]] | None = None,
) -> None:
    """Render global velocity (>0.5M views) and negative sentiment (>50%) alerts across all dashboard pages."""
    velocity_key = f"velocity_tracking_{library_owner_id(user_email)}"
    velocity_state = st.session_state.get(velocity_key)
    cards = velocity_state.get("cards", []) if isinstance(velocity_state, dict) else []

    velocity_items = find_high_velocity_posts(cards, _session_posts())
    sentiment_flags = find_negative_sentiment_flags(
        analysis_results=st.session_state.get("analysis_last_results"),
        processed_rows=st.session_state.get("processed_rows"),
        sentiment_history=sentiment_history,
        news_scan=st.session_state.get("news_scan"),
        news_history=news_history,
    )

    velocity_message = format_velocity_alert(velocity_items)
    sentiment_message = format_negative_sentiment_alert(sentiment_flags)
    if not velocity_message and not sentiment_message:
        return

    with st.container(key="sm_global_alerts_banner"):
        if velocity_message:
            st.warning(velocity_message, icon=":material/trending_up:")
        if sentiment_message:
            st.warning(sentiment_message, icon=":material/flag:")
