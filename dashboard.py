"""Frontend-first Streamlit dashboard for CrisisRadar."""

from __future__ import annotations

import asyncio
import html
import hashlib
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st

from modules.audio_feedback import install_audio_feedback, notify_sound
from modules.auth import require_access
from modules.ui import apply_brand, running_glow, show_running_glow
from modules.browserbase_usage_ui import render_browserbase_usage_bar
from modules.global_alerts import render_global_alerts
from modules.analysis_library import (
    SOURCE_LABELS, dataset_date_label, dataset_key, dataset_options, matches_sources,
    select_history, select_records,
)
from modules.analysis_dataset_ui import render_dataset_editor
from modules.recent_collection import collection_snapshot, standard_dataset_title
from modules.record_management_ui import render_record_management
from modules.dataset_cards import (
    apply_dataset_card_theme, featured_datasets, local_dataset_date, render_dataset_cards, viewer_timezone,
)
from modules.record_state import purge_record_caches
from modules.navigation import dashboard_views
from modules.scrape_navigation import FACEBOOK_USAGE_WARNING, render_scrape_navigation
from modules.content_recommendations_ui import render_content_workspace
from modules.tutorial_ui import render_tutorial_workspace
from modules.velocity_tracking_ui import render_velocity_workspace
from modules.ai_errors import AIError, redact
from modules.config import AI_PROVIDERS, get_ai_key, get_ai_model, get_ai_provider, get_setting, load_environment
from modules.database import delete_processed_posts, get_post_id, init_db, upsert_post
from modules.gencite_ui import render_gencite_workspace
from modules.client_report import (
    CSS_PATH as REPORT_CSS, content_type, negative_records, npr_sheet, platform_label, report_document,
    report_number, report_record, sentiment_sheet,
)
from modules.engagement import checks_for
from modules.report_pdf import PDFExportError, render_pdf
from modules.word_cloud_ui import render_word_cloud
from modules.facebook_session import (
    FacebookLoginError,
    FacebookSession,
    capture_facebook_session,
    check_facebook_session,
    facebook_storage_path,
    saved_facebook_session,
)
from modules.facebook_remote import (
    forget_login, login_job, remote_facebook_configured, remote_facebook_session, start_login,
)
from modules.facebook_links_ui import forget_removed_facebook_links, render_facebook_link_list, render_saved_facebook_links
from modules.facebook_search import OYOSHI_SEARCH_URL, validate_facebook_search_url
from modules.collection_jobs import active_collection, cancel_collection, collection_job, start_collection
from modules.processor import analyze_posts
from modules.gemini import AUTO_MODEL, GeminiError, list_text_models, normalize_model_name
from modules.reddit import CAMBODIA_URL, is_reddit_url, reddit_configured
from modules.x import READ_PRICE_USD, X_SEARCHES, XClient, XError, collect_x, estimate_reads, x_configured
from modules.websites import MAX_LINKS as MAX_WEBSITE_LINKS, WebsiteError, collect_websites, parse_website_links
from modules.website_presets_ui import render_website_presets
from modules.signals import NON_CONTENT_TYPES, SIGNAL_LABELS, is_analyzable, is_flagged, watch_terms
from modules.saved_links import is_facebook_link, library_owner_id, load_saved_links, parse_links, remove_saved_links, save_links
from modules.sentiment_history import (
    NEWS_SOURCE, chart_spec, combined_history, load_history, news_sentiment_rows, persist_analysis_run,
    reset_timeline,
)
from modules.google_news import (
    EDITIONS, MAX_ARTICLES, NEWS_CATEGORIES, TIME_WINDOWS, browse_url, build_scan_snapshot, collect_news,
    daily_flag_counts,
)
from modules.news_risk import daily_chart_spec, load_news_history, persist_news_scan, risk_chart_spec

try:
    from modules.scraper import scrape_group
    SCRAPER_AVAILABLE = True
except Exception:
    SCRAPER_AVAILABLE = False

IS_CLOUD = os.path.exists("/mount/src") or os.getenv("SENSEMAKER_HOSTED") == "1"

AUTH_LOGIN_TIMEOUT_SECONDS = 240


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _remember_collection(rows: list[dict[str, Any]], source: str, completed_at: float | None = None) -> None:
    """Called once when a collector finishes, including empty/partially failed runs."""
    completed_at = completed_at or time.time()
    previous = st.session_state.get("last_collected_dataset")
    # A background Facebook job can be observed after a newer synchronous scrape.
    if previous and previous["completed_at"] > completed_at:
        return
    st.session_state["last_collected_dataset"] = collection_snapshot(rows, source, completed_at)
    if any(is_analyzable(row) for row in rows):
        notify_sound("success")


def _collection_record_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    return {(str(row.get("url") or "").strip(), str(row.get("scraped_at") or ""),
             str(row.get("source_type") or "")) for row in rows
            if str(row.get("url") or "").strip() and row.get("source_type") not in NON_CONTENT_TYPES}


def _write_results_to_firestore(posts: List[Dict[str, Any]], custom_title: str) -> Dict[str, int]:
    """Upsert scraped posts into Firestore and return summary counts."""
    db = init_db()
    inserted = 0
    skipped = 0
    save_timestamp = _now_iso()
    safe_title = custom_title.strip()

    for post in posts:
        url = str(post.get("url", "")).strip()
        if not url or post.get("source_type") in NON_CONTENT_TYPES:
            skipped += 1
            continue
        payload = dict(post)
        payload["custom_title"] = safe_title
        payload["saved_at"] = save_timestamp
        if not payload.get("scraped_at"):
            payload["scraped_at"] = save_timestamp
        upsert_post(db, payload)
        inserted += 1
    st.session_state.pop("analysis_dataset_catalog", None)
    st.session_state.pop("record_manager_rows", None)
    st.session_state.pop("record_manager_pending", None)
    saved_keys = _collection_record_keys(posts)
    for key in ("news_scan", "x_collection", "website_collection"):
        collection = st.session_state.get(key) or {}
        if saved_keys and collection.get("cloud_save") and saved_keys == _collection_record_keys(collection.get("rows", [])):
            collection["cloud_save"].update(state="saved", title=safe_title, count=inserted)
    recent = st.session_state.get("last_collected_dataset")
    if recent and recent["rows"] and {
        (row["url"], row.get("scraped_at")) for row in recent["rows"]
    } == {(str(row.get("url") or "").strip(), row.get("scraped_at")) for row in posts if is_analyzable(row)}:
        recent.update(saved=True, title=safe_title)
    return {"inserted": inserted, "skipped": skipped}


def _fetch_last_scrape_posts(recent: dict[str, Any], status_filter: str, limit: int | None) -> list[dict[str, Any]]:
    """Read only this scrape's IDs; reused titles and older saves cannot expand it."""
    db = init_db()
    collection = db.collection(get_setting("FIREBASE_COLLECTION", "raw_posts"))
    ids = list(dict.fromkeys(row["_id"] for row in recent["rows"]))
    allowed_ids = set(ids)
    rows = []
    for start in range(0, len(ids), 200):
        for doc in db.get_all([collection.document(ident) for ident in ids[start:start + 200]], timeout=30):
            if doc.exists and doc.id in allowed_ids:
                row = {**(doc.to_dict() or {}), "_id": doc.id}
                if status_filter == "all" or row.get("status") == status_filter:
                    rows.append(row)
    return select_records(rows, source_urls=None, limit=len(rows) if limit is None else limit)


def _fetch_firestore_posts(
    status_filter: str, limit: int | None, source_urls: list[str] | None = None,
    dataset: str | list[str] | None = None,
) -> List[Dict[str, Any]]:
    db = init_db()
    query = db.collection(get_setting("FIREBASE_COLLECTION", "raw_posts"))
    if status_filter != "all":
        query = query.where("status", "==", status_filter)
    docs = query.stream(timeout=30)
    rows: List[Dict[str, Any]] = []
    for doc in docs:
        item = doc.to_dict() or {}
        item["_id"] = doc.id
        item["record_created_at"] = getattr(doc, "create_time", None)
        rows.append(item)
    return select_records(rows, source_urls=source_urls, limit=len(rows) if limit is None else limit, dataset=dataset)


def _clear_loaded_analysis() -> None:
    st.session_state["analysis_editor_revision"] = st.session_state.get("analysis_editor_revision", 0) + 1
    for key in ("firestore_rows", "processed_rows", "analysis_last_results", "gemini_last_results",
                "firestore_load_error", "firestore_last_load_count", "analysis_record_selection",
                "processed_delete_selection", "processed_delete_pending", "processed_delete_notice",
                "content_recommendation_plan", "content_records", "analysis_editor_key", "analysis_editor_frame", "analysis_edits_saved"):
        st.session_state.pop(key, None)


def _fetch_analysis_datasets(source_urls: list[str] | None) -> list[dict[str, Any]]:
    # Only metadata is needed to choose a dataset; source text loads on request.
    query = init_db().collection(get_setting("FIREBASE_COLLECTION", "raw_posts")).select([
        "custom_title", "source_type", "platform", "target_url", "url", "status", "scraped_at", "saved_at",
    ])
    rows = [{**(doc.to_dict() or {}), "record_created_at": getattr(doc, "create_time", None)}
            for doc in query.stream(timeout=30)]
    return dataset_options(rows, source_urls)


def _toggle_analysis_dataset(key: str) -> None:
    selected = list(st.session_state.get("analysis_datasets", []))
    st.session_state["analysis_datasets"] = ([item for item in selected if item != key]
                                            if key in selected else [*selected, key])


def _render_analysis_datasets(source_urls: list[str] | None, ready: bool) -> list[str]:
    def refresh() -> None:
        st.session_state.pop("analysis_dataset_catalog", None)
        _clear_loaded_analysis()

    st.button("", icon=":material/refresh:", key="refresh_analysis_datasets", help="Refresh datasets",
              on_click=refresh, disabled=not ready)
    recent = st.session_state.get("last_collected_dataset")
    if ready and isinstance(recent, dict) and recent.get("rows") and not recent.get("saved"):
        st.session_state.pop("analysis_dataset_catalog", None)
    if ready and "analysis_dataset_catalog" not in st.session_state:
        try:
            if isinstance(recent, dict) and recent.get("rows") and not recent.get("saved"):
                _write_results_to_firestore(recent["rows"], custom_title=recent["title"])
                recent["saved"] = True
            st.session_state["analysis_dataset_catalog"] = {"rows": _fetch_analysis_datasets(source_urls), "error": False}
        except Exception:
            st.session_state["analysis_dataset_catalog"] = {"rows": [], "error": True}
    catalog = st.session_state.get("analysis_dataset_catalog", {}) if ready else {}
    if catalog.get("error"):
        st.error("Datasets could not be loaded. Click the refresh icon to retry.")
    options = {row["key"]: row for row in catalog.get("rows", [])}
    local_tz = viewer_timezone()
    def label(key: str) -> str:
        row = options[key]
        return (f"{row['title']} · {local_dataset_date(row, local_tz, seconds=True)} · {', '.join(row.get('sources', []))} · "
                f"{row['count']} record{'s' if row['count'] != 1 else ''} · {row['pending']} pending")

    # Preserve a single choice from sessions opened before multiple selection existed.
    legacy = st.session_state.pop("analysis_dataset", None)
    current = st.session_state.get("analysis_datasets", [legacy] if legacy else [])
    st.session_state["analysis_datasets"] = list(dict.fromkeys(key for key in current if key in options))
    if options:
        apply_dataset_card_theme()
        is_today, featured = featured_datasets(list(options.values()), datetime.now(local_tz))
        st.markdown("#### Today's datasets" if is_today else "#### Latest datasets")
        st.caption((f"{len(featured)} datasets saved today" if is_today else
                    f"No datasets saved today. Showing the {len(featured)} latest datasets")
                   + f" · Newest first · Times in {local_tz}")
        st.caption("Select one or more cards to analyze together. Select a chosen card again to remove it.")
        render_dataset_cards(featured, "featured", local_tz, key_prefix="analysis_dataset",
                             on_select=_toggle_analysis_dataset, selected=st.session_state["analysis_datasets"])
    with st.expander("Choose older datasets or adjust selection"):
        selected = st.multiselect("Datasets to analyze", list(options), key="analysis_datasets",
                                  placeholder="Search all saved datasets", disabled=not ready or not options,
                                  format_func=label,
                                  help="Includes today's and older datasets from your selected sources. "
                                       "Type a dataset name, source or date to find it.")
    if selected:
        st.caption(f"{len(selected)} dataset{'s' if len(selected) != 1 else ''} selected · "
                   f"{sum(options[key]['count'] for key in selected):,} records")
        st.text("Selected: " + ", ".join(options[key]["title"] for key in selected))
    if ready and not options and not catalog.get("error"):
        st.info("No saved datasets match these sources. Collect and save data, then refresh datasets.")
    return selected


def _sync_analysis_datasets(datasets: list[str], status: str, limit: int | None = None) -> None:
    context = (tuple(sorted(set(datasets))), status, limit)
    if st.session_state.get("analysis_dataset_context") != context:
        _clear_loaded_analysis()
        st.session_state["analysis_dataset_context"] = context


def _analysis_source_checkbox_key(source: str) -> str:
    return f"analysis_source_{source}"


def _selected_analysis_sources(fallback: list[str] | None = None) -> list[str]:
    if any(_analysis_source_checkbox_key(source) in st.session_state for source in SOURCE_LABELS):
        return [source for source in SOURCE_LABELS if st.session_state.get(_analysis_source_checkbox_key(source))]
    return list(st.session_state.get("analysis_sources", fallback or []))


def _render_analysis_sources(user_email: str) -> tuple[list[str] | None, bool]:
    st.session_state.pop("analysis_source_scope", None)
    saved = set(st.session_state.get("analysis_sources") or [])
    for source in SOURCE_LABELS:
        st.session_state.setdefault(_analysis_source_checkbox_key(source), source in saved)
    st.markdown("**Filter by record sources**")
    columns = st.columns(3)
    sources = [
        source
        for index, (source, label) in enumerate(SOURCE_LABELS.items())
        if columns[index % len(columns)].checkbox(label, key=_analysis_source_checkbox_key(source))
    ]
    st.session_state["analysis_sources"] = sources
    ready = bool(sources)
    if not ready:
        st.info("Choose at least one record source to see its saved datasets.")
    context = (get_setting("FIREBASE_COLLECTION", "raw_posts"), "explicit-sources", tuple(sorted(sources)))
    if st.session_state.get("analysis_library_context") != context:
        # Includes sessions created before source selection existed. No stale rows
        # or prior AI results can survive a change of source library.
        _clear_loaded_analysis()
        for key in ("analysis_dataset_catalog", "analysis_dataset", "analysis_datasets", "analysis_dataset_context"):
            st.session_state.pop(key, None)
        st.session_state["analysis_library_context"] = context
    return sources, ready


def _process_selected_firestore_posts(
    selected_posts: List[Dict[str, Any]],
    provider: str = "gemini",
    status_callback: Any = None,
) -> Dict[str, Any]:
    """Run the selected provider on posts and persist results to Firestore.

    Returns dict with 'processed', 'error' counts and 'analyses' list.
    """
    db = init_db()
    model_name = (st.session_state.get("gemini_model_name", get_ai_model(provider))
                  if provider == "gemini" else get_ai_model(provider))
    # The watch-terms box lives on a step that may be hidden while the run starts.
    targets = watch_terms(st.session_state.get("thailand_watch_terms")
                          or st.session_state.get(ANALYSIS_WATCH_MEMO_KEY, ""))
    analysis_by_id = analyze_posts(
        selected_posts,
        provider=provider,
        model_name=model_name,
        on_status=status_callback,
        targets=targets,
    )
    snapshot = persist_analysis_run(db, selected_posts, analysis_by_id, provider=provider, targets=targets)
    _load_sentiment_history.clear()
    completed_at = (snapshot or {}).get("completed_at") or _now_iso()
    processed = 0
    errored = 0
    analyses: List[Dict[str, Any]] = []
    for post in selected_posts:
        doc_id = post.get("_id")
        if not doc_id:
            errored += 1
            continue
        analysis = analysis_by_id.get(doc_id)
        if analysis:
            post.update(status="processed", analysis=analysis, processed_at=completed_at)
            processed += 1
            analyses.append({
                **analysis,
                "_id": doc_id,
                "platform": post.get("platform", "web"),
                "source_type": post.get("source_type", ""),
                "created_at": post.get("created_at", ""),
                "parent_url": post.get("parent_url", ""),
                "url": post.get("url", ""),
                "raw_text_preview": str(post.get("raw_text", ""))[:200],
                "translation": analysis.get("translation", ""),
                "sentiment": analysis.get("sentiment", ""),
                "risk_score": analysis.get("risk_score", ""),
                "topics": ", ".join(analysis.get("topics", [])) if isinstance(analysis.get("topics"), list) else str(analysis.get("topics", "")),
                # Fields for the client report templates.
                "raw_text": str(post.get("raw_text", ""))[:2000],
                "title": post.get("title", ""),
                "publisher": post.get("publisher", ""),
                "scraped_at": post.get("scraped_at", ""),
                "comment_count": post.get("comment_count", 0),
                "checks": checks_for(post),
                "processed_at": completed_at,
            })
        else:
            post["status"] = "error"
            errored += 1
    return {"processed": processed, "error": errored, "analyses": analyses,
            "errors": list(getattr(analysis_by_id, "errors", [])), "history_run": snapshot}


@st.cache_data(ttl=60, show_spinner=False)
def _load_sentiment_history() -> list[dict[str, Any]]:
    return load_history(init_db())


def _render_sentiment_history(source_urls: list[str] | None = None) -> None:
    with st.container(border=True, key="sentiment_history"):
        if source_urls is None:
            st.caption("Showing saved history from all record sources.")
        if st.button("Refresh timeline", key="refresh_sentiment_history"):
            _load_sentiment_history.clear()
            _load_news_history.clear()
        try:
            analysis_rows = _load_sentiment_history()
        except Exception:
            st.warning("Sentiment history could not be loaded. Refresh the timeline to try again.")
            return
        visible_analysis = select_history(analysis_rows, source_urls)
        analysis_rows = visible_analysis
        try:
            news_rows = (news_sentiment_rows(_load_news_history())
                         if source_urls is None or "source:news" in source_urls else [])
        except Exception:
            news_rows = []
            st.caption("Online News points couldn't be loaded. Refresh the timeline to try again.")
        rows = combined_history(analysis_rows, news_rows)
        if not rows:
            st.info("Run an analysis or search Online News to start your timeline. Each result is saved here automatically.")
            return
        average = sum(row["score"] for row in rows) / len(rows)
        latest = rows[-1]["score"]
        delta = f"{latest - rows[-2]['score']:+.1f} vs previous point" if len(rows) > 1 else None
        first, second, third, fourth = st.columns(4)
        first.metric("Latest sentiment", f"{latest:+.1f}", delta, help=f"From {rows[-1]['source']}")
        second.metric("Average sentiment", f"{average:+.1f}")
        third.metric("Analysis runs", len(analysis_rows))
        fourth.metric("News searches", len(news_rows))
        st.vega_lite_chart(spec=chart_spec(rows), use_container_width=True)
        st.caption("−100 = all negative · 0 = balanced or neutral · +100 = all positive. "
                   "AI analysis: Joy counts as positive, Anger and Anxiety as negative, using successful records only. "
                   "Online News: positive minus negative headlines, from a keyword scan. "
                   "Each point has equal weight in the average.")
        if len(rows) == 1:
            st.caption("Your first point is saved. Run another analysis or news search to see the direction of change.")
        with st.expander("History"):
            st.dataframe(pd.DataFrame([{
                "Completed (UTC)": row["completed_at"],
                "Source": row["source"],
                "Sentiment": round(row["score"], 1),
                "Analyzed": row["processed_count"],
                "Failed": row["error_count"],
                "Positive": row["counts"]["Joy"] if "counts" in row else row["tone_counts"].get("Positive", 0),
                "Neutral": row["counts"]["Neutral"] if "counts" in row else row["tone_counts"].get("Neutral", 0),
                "Negative": (row["counts"]["Anger"] + row["counts"]["Anxiety"] if "counts" in row
                             else row["tone_counts"].get("Negative", 0)),
                "Method": ("Headline keywords" if row["source"] == NEWS_SOURCE
                           else AI_PROVIDERS.get(row["provider"], row["provider"])),
                "Watch terms": ", ".join(row.get("targets", [])),
            } for row in reversed(rows)]), use_container_width=True, hide_index=True)


def _reset_sentiment_timeline() -> None:
    if not st.session_state.pop("confirm_timeline_reset", False):
        return
    try:
        counts = reset_timeline(init_db())
    except Exception:
        logging.getLogger(__name__).exception("Sentiment trends timeline reset could not be completed")
        st.session_state["timeline_reset_notice"] = (
            "error", "The timeline reset could not be completed. Some history may already have been removed. "
            "Review the refreshed timelines and try again.",
        )
    else:
        st.session_state["timeline_reset_notice"] = (
            "success", f"Timeline reset. Removed {counts['analysis_runs']} analysis run(s) and "
            f"{counts['news_scans']} news scan(s). New analyses and news searches will start a fresh timeline.",
        )
    finally:
        _load_sentiment_history.clear()
        _load_news_history.clear()


def _render_sentiment_trends_settings() -> None:
    st.subheader("Timeline settings")
    notice_area = st.empty()
    with st.container(border=True, key="timeline_reset_settings"):
        st.markdown("#### Reset timeline")
        st.write("Start fresh by clearing the saved AI analysis runs and Online News scans from "
                 "both the sentiment and border tension risk timelines.")
        st.caption("Collected records, datasets, and per-record analysis results are kept. "
                   "This resets shared history for everyone using this library.")
        if st.button("Reset timeline", key="reset_sentiment_timeline"):
            st.session_state["confirm_timeline_reset"] = True
        if st.session_state.get("confirm_timeline_reset"):
            confirmation = st.empty()
            with confirmation.container():
                st.warning("Permanently delete all saved timeline history for this library? "
                           "This clears both timelines for all users and cannot be undone.")
                confirm, cancel = st.columns(2)
                confirmed = confirm.button("Confirm reset timeline", key="confirm_reset_sentiment_timeline",
                                           type="primary")
                cancel.button("Cancel", key="cancel_reset_sentiment_timeline",
                              on_click=lambda: st.session_state.pop("confirm_timeline_reset", None))
            if confirmed:
                # Run after main() has rechecked access, rather than in a pre-run callback.
                _reset_sentiment_timeline()
                confirmation.empty()
    notice = st.session_state.pop("timeline_reset_notice", None)
    if notice:
        getattr(notice_area, notice[0])(notice[1])


def _render_sentiment_trends() -> None:
    sentiment_tab, risk_tab, settings_tab = st.tabs([
        "Sentiment over time", "Border tension risk over time", "Settings",
    ])
    # Apply a confirmed reset before reading either timeline in this run.
    with settings_tab:
        _render_sentiment_trends_settings()
    with sentiment_tab:
        # The saved timeline is independent of the inputs for the next analysis.
        _render_sentiment_history()
    with risk_tab:
        _render_news_risk_history()


def _render_gemini_settings() -> None:
    api_key = get_ai_key("gemini")
    fingerprint = hashlib.sha256(api_key.strip().encode()).hexdigest()
    if st.session_state.get("gemini_catalog_key") != fingerprint:
        st.session_state["gemini_catalog_key"] = fingerprint
        st.session_state.pop("gemini_available_models", None)
        st.session_state.pop("gemini_catalog_error", None)
    if st.button("", icon=":material/refresh:", key="refresh_gemini_models", help="Refresh available models",
                 disabled=not api_key):
        try:
            with st.spinner("Checking available Gemini models…"):
                st.session_state["gemini_available_models"] = list_text_models(api_key.strip())
            st.session_state.pop("gemini_catalog_error", None)
        except GeminiError as exc:
            st.session_state.pop("gemini_available_models", None)
            st.session_state["gemini_catalog_error"] = str(exc)
    if st.session_state.get("gemini_catalog_error"):
        st.error(st.session_state["gemini_catalog_error"])
    available = st.session_state.get("gemini_available_models")
    options = [AUTO_MODEL] + (available or [])
    try:
        current = normalize_model_name(st.session_state.get("gemini_model_name", get_ai_model("gemini")))
    except GeminiError:
        current = AUTO_MODEL
    if current not in options:
        if available is None:
            options.append(current)  # Preserve explicit settings until Google validates them.
        else:
            st.info(f"{current} was not returned by Google. Switched to Automatic.")
            current = AUTO_MODEL
    st.session_state["gemini_model_name"] = current
    st.selectbox("Gemini model", options, key="gemini_model_name",
                 format_func=lambda name: "Automatic · available Flash model" if name == AUTO_MODEL else name.removeprefix("models/"))
    st.caption("Automatic checks the available models before analysis. Model availability does not guarantee remaining quota.")


PROVIDER_LOGOS = {"gemini": "/app/static/gemini.svg", "openai": "/app/static/chatgpt.svg"}
PROVIDER_RADIO_CSS = """<style>
.st-key-ai_provider [data-testid="stRadio"] [role="radiogroup"] { gap: 1.5rem; }
.st-key-ai_provider [data-testid="stRadioOption"] p { display: flex; align-items: center; gap: 0.5rem; }
.st-key-ai_provider [data-testid="stRadioOption"] img { width: 1.5rem; height: 1.5rem; max-height: none; margin: 0; }
</style>"""


def _render_ai_settings() -> str:
    st.html(PROVIDER_RADIO_CSS)
    if "ai_provider" not in st.session_state:
        st.session_state["ai_provider"] = get_ai_provider()
    provider = st.radio("AI provider", list(AI_PROVIDERS), key="ai_provider", horizontal=True,
                        format_func=lambda key: f"![]({PROVIDER_LOGOS[key]}) {AI_PROVIDERS[key]}")
    if not get_ai_key(provider):
        st.warning(f"{AI_PROVIDERS[provider]} is not configured yet. Choose another provider or contact the app administrator.")
    if provider == "gemini":
        _render_gemini_settings()
    else:
        st.caption(f"ChatGPT model: {get_ai_model(provider)}")
    return provider


def _run_scrape_sync(url: str, headless: bool, scroll_times: int) -> List[Dict[str, Any]]:
    if sys.platform.startswith("win") and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        # Playwright needs subprocess support; Proactor loop is the stable option on Windows.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    async def _runner() -> List[Dict[str, Any]]:
        return await scrape_group(
            target_url=url,
            headless=headless,
            scroll_min=scroll_times,
            scroll_max=scroll_times,
        )

    return asyncio.run(_runner())


async def _run_multi_scrape_async(
    urls: List[str], headless: bool, scroll_times: int,
    concurrency: int = 3,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    reddit_limit: int = 50, reddit_comments: int = 20,
    facebook_owner: str | None = None,
    on_results: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    strict_scroll_limit: bool = False,
    on_activity: Optional[Callable[[str, str, int], None]] = None,
) -> List[Dict[str, Any]]:
    # Persistent remote profiles must not be opened concurrently.
    semaphore = asyncio.Semaphore(1 if facebook_owner else max(1, concurrency))
    completed = 0

    async def scrape_one(url: str) -> List[Dict[str, Any]]:
        try:
            rows = await scrape_group(
                target_url=url,
                headless=headless,
                scroll_min=scroll_times,
                scroll_max=scroll_times,
                reddit_limit=reddit_limit, reddit_comments=reddit_comments,
                **({"facebook_owner": facebook_owner} if facebook_owner else {}),
                **({"strict_scroll_limit": True} if strict_scroll_limit else {}),
                **({"on_progress": lambda message, posts: on_activity(url, message, posts)} if on_activity else {}),
            )
            # Preserve which input URL produced each row.
            for row in rows:
                row["target_url"] = url
            return rows
        except Exception as exc:
            return [
                {
                    "url": url,
                    "target_url": url,
                    "raw_text": f"Scrape failed for input URL. Reason: {str(exc).strip() or repr(exc)}",
                    "comments": [],
                    "comment_count": 0,
                    "source_type": "batch_error",
                    "facebook_connection_error": bool(facebook_owner and isinstance(exc, FacebookLoginError)),
                }
            ]

    async def bounded_scrape(url: str) -> List[Dict[str, Any]]:
        nonlocal completed
        async with semaphore:
            if on_activity:
                on_activity(url, "Starting collection", 0)
            rows = await scrape_one(url)
            if on_activity:
                posts = sum(row.get("source_type") not in NON_CONTENT_TYPES for row in rows)
                on_activity(url, "Source finished", posts)
            completed += 1
            if on_progress:
                on_progress(completed, len(urls), url)
            return rows

    if facebook_owner:
        batches = []
        for index, url in enumerate(urls):
            rows = await bounded_scrape(url)
            batches.append(rows)
            if on_results:
                on_results([row for batch in batches for row in batch])
            connection_errors = [row for row in rows if row.get("facebook_connection_error") or row.get("collection_stopped")]
            if connection_errors:
                remaining = len(urls) - index - 1
                if remaining:
                    connection_errors[0]["raw_text"] += f" Collection stopped; {remaining} remaining links were not attempted."
                break
    else:
        batches = await asyncio.gather(*(bounded_scrape(url) for url in urls))
    merged: List[Dict[str, Any]] = []
    for batch in batches:
        merged.extend(batch)
    return merged


def _run_multi_scrape_sync(*args, **kwargs) -> List[Dict[str, Any]]:
    if sys.platform.startswith("win") and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(_run_multi_scrape_async(*args, **kwargs))


@st.cache_data(ttl=300, show_spinner=False)
def _check_facebook_session(path: str, modified: int, size: int) -> FacebookSession:
    # Recheck whenever login saves a new file, and at least every five minutes.
    return check_facebook_session(path)


def _get_facebook_session(path: str) -> FacebookSession:
    saved = saved_facebook_session(path)
    if saved.status in {"connected", "disconnected"}:
        return saved
    if IS_CLOUD:
        return saved
    try:
        info = Path(path).stat()
    except OSError:
        return FacebookSession()
    with st.spinner("Checking Facebook connection…"):
        return _check_facebook_session(path, info.st_mtime_ns, info.st_size)


def _facebook_storage_path() -> str:
    return facebook_storage_path()


@st.fragment(run_every=2)
def _render_remote_facebook_progress(email: str, job_id: str) -> None:
    # Fragment reruns bypass main(), so recheck the signed-in identity here too.
    from modules.auth import allowed_emails, verify_access
    try:
        if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != email:
            raise PermissionError()
    except Exception:
        job = login_job(email, job_id)
        if job:
            job.cancel.set()
        st.rerun(scope="app")
    job = login_job(email, job_id)
    if job is None or job.status in {"complete", "error"}:
        st.session_state.pop("facebook_login_job", None)
        if job and job.error:
            st.session_state["facebook_login_error"] = job.error
        elif job and job.status == "complete":
            st.session_state["facebook_login_notice"] = "Facebook login saved. You can now collect posts."
        elif job is None:
            st.session_state["facebook_login_error"] = "The login session ended. Open the Facebook login window again."
        forget_login(email, job_id)
        st.rerun(scope="app")
    # A running status keeps its spinner visible between background-job polls.
    # A spinner context would disappear as soon as this fragment finishes.
    if job.status == "saving":
        st.status("Saving your Facebook login…", state="running")
    elif job.live_url:
        import streamlit.components.v1 as components
        st.info("Finish Facebook sign-in and any verification below, then click Save Facebook login. "
                "The window stays open for up to three minutes.")
        if job.notice:
            st.warning(job.notice)
        components.iframe(job.live_url, height=650, scrolling=True)
        st.link_button("Open login in a new tab", job.live_url)
        if st.button("Save Facebook login", key="save_remote_facebook_login", type="primary",
                     disabled=job.status != "waiting" or job.finish.is_set()):
            job.finish.set()
        if job.status == "checking" or job.finish.is_set():
            st.status("Checking your Facebook sign-in…", state="running")
    elif job.status == "checking":
        st.status("Checking your saved Facebook connection…", state="running")
    else:
        st.status("Opening your Facebook login window…", state="running")
    if st.button("Cancel Facebook login", key="cancel_remote_facebook_login"):
        job.cancel.set()


def _render_remote_facebook_connection(email: str, session: FacebookSession) -> FacebookSession:
    if active_collection(email):
        return session
    if st.session_state.get("facebook_login_notice"):
        st.success(st.session_state.pop("facebook_login_notice"))
    if st.session_state.get("facebook_login_error"):
        st.error(st.session_state.pop("facebook_login_error"))
    job_id = st.session_state.get("facebook_login_job")
    if job_id:
        _render_remote_facebook_progress(email, job_id)
        return session
    if session.status == "connected":
        return session

    def begin(check_only: bool = False):
        from modules.auth import allowed_emails, verify_access
        try:
            if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != email:
                return
        except Exception:
            return  # main() will rerun the authorization gate before rendering.
        st.session_state["facebook_login_job"] = start_login(email, check_only=check_only)

    with st.container(border=True, key="facebook_connection"):
        if session.status == "saved":
            st.info("Your Facebook login is saved. Check the connection before collecting again.")
            st.button("Check Facebook connection", key="check_fb_session_btn", on_click=begin, args=(True,))
            st.button("Log in to Facebook", key="capture_fb_session_btn", on_click=begin)
        else:
            st.subheader("Connect your Facebook account")
            st.caption("Sign in through the interactive browser. Your saved login is private to your CrisisRadar account.")
            st.button("Log in to Facebook", key="capture_fb_session_btn", type="primary",
                      use_container_width=True, disabled=not remote_facebook_configured(), on_click=begin)
        if not remote_facebook_configured():
            st.caption("The administrator needs to finish configuring the remote Facebook browser.")
    return session


def _render_facebook_connection(session: Optional[FacebookSession] = None, user_email: str = "") -> FacebookSession:
    if IS_CLOUD:
        return _render_remote_facebook_connection(user_email, session or remote_facebook_session(user_email))
    path = _facebook_storage_path()
    if session is None:
        session = _get_facebook_session(path)
    if session.status in {"connected", "saved"}:
        os.environ["PLAYWRIGHT_STORAGE_STATE"] = path
        if session.status == "saved":
            with st.container(border=True, key="facebook_connection"):
                st.info("Your Facebook login is saved on this device and will be reused when collecting posts.")
                st.caption("The connection could not be confirmed right now. Your saved session has been kept.")
                if st.button("Check Facebook connection", key="check_fb_session_btn"):
                    _check_facebook_session.clear()
                    st.rerun()
            return session
        st.empty()  # Clear the previous login card on the successful-login rerun.
        return session
    with st.container(border=True, key="facebook_connection"):
        identity, action = st.columns([3, 1], vertical_alignment="center")
        identity.markdown(
            '<div class="fb-identity"><div class="fb-avatar" aria-hidden="true">f</div>'
            '<div><p class="fb-label">FACEBOOK</p><p class="fb-name">Connect your account</p>'
            '<p class="fb-detail">Log in once to collect Facebook posts.</p></div></div>',
            unsafe_allow_html=True,
        )
        if action.button(
            "Log in to Facebook", key="capture_fb_session_btn", type="primary",
            use_container_width=True,
        ):
            with st.spinner("Finish logging in in the browser window…"):
                try:
                    capture_facebook_session(path, timeout_seconds=AUTH_LOGIN_TIMEOUT_SECONDS)
                    os.environ["PLAYWRIGHT_STORAGE_STATE"] = path
                    _check_facebook_session.clear()
                    st.rerun()
                except FacebookLoginError as exc:
                    st.error(str(exc))
                except Exception:
                    logging.getLogger(__name__).exception("Facebook login failed")
                    st.error("Facebook login could not start or be saved. Please try again. "
                             "The error details have been recorded in the CrisisRadar server log.")
        if session.status == "unavailable":
            st.caption("We couldn’t confirm your saved session. Log in again to reconnect.")
    return session


def _render_link_workspace(user_email: str, platform: str | None = None) -> tuple[List[str], bool]:
    owner = library_owner_id(user_email)
    if platform:
        owner = f"{owner}_{platform}"
    default_links = [CAMBODIA_URL] if platform != "facebook" else []

    def widget_key(name: str) -> str:
        return f"{name}_{platform}" if platform else name

    def matches_source(url: str) -> bool:
        if platform == "reddit":
            return is_reddit_url(url)
        if platform == "facebook":
            return is_facebook_link(url)
        return True

    def parse_sources(text: str) -> tuple[list[str], list[str]]:
        urls, invalid = parse_links(text)
        return [url for url in urls if matches_source(url)], invalid + [
            url for url in urls if not matches_source(url)]

    library_key = f"saved_links_{owner}"
    selection_key = f"selected_links_{owner}"
    input_key = f"new_links_{owner}"
    message_key = f"links_message_{owner}"
    save_error_key = f"links_save_error_{owner}"
    defaults_key = f"default_links_initialized_{owner}"

    def refresh() -> None:
        try:
            with st.spinner("Loading saved links…"):
                urls = [url for url in load_saved_links(init_db(), user_email) if matches_source(url)]
            st.session_state[library_key] = {"urls": urls, "error": False}
        except Exception:
            previous = st.session_state.get(library_key, {})
            st.session_state[library_key] = {"urls": previous.get("urls", []), "error": True}

    if library_key not in st.session_state:
        refresh()
    library = st.session_state[library_key]
    saved = list(dict.fromkeys(default_links + library["urls"]))
    saved_set = set(saved)
    memo_key = f"links_selection_memo_{owner}"
    if selection_key not in st.session_state and st.session_state.get(memo_key):
        # Streamlit drops widget state while this step is hidden; restore the earlier choice.
        st.session_state[selection_key] = list(st.session_state[memo_key])
    if not st.session_state.get(defaults_key):
        st.session_state[selection_key] = list(dict.fromkeys(
            st.session_state.get(selection_key, []) + default_links))
        st.session_state[defaults_key] = True
    # A refresh may include removals made on another device.
    if selection_key in st.session_state:
        st.session_state[selection_key] = [url for url in st.session_state[selection_key] if url in saved_set]

    def select_all() -> None:
        st.session_state[selection_key] = saved.copy()

    def clear_selection() -> None:
        st.session_state[selection_key] = []
        st.session_state[input_key] = ""
        st.session_state.pop(save_error_key, None)

    def remove_selected() -> bool:
        removable = [url for url in st.session_state.get(selection_key, []) if url != CAMBODIA_URL]
        try:
            remove_saved_links(init_db(), user_email, removable)
            removed = set(removable)
            st.session_state[library_key]["urls"] = [url for url in saved if url not in removed]
            st.session_state[memo_key] = [
                url for url in st.session_state.get(selection_key, []) if url not in removed]
            if platform == "facebook":
                forget_removed_facebook_links(user_email, removable)
            st.session_state[message_key] = f"Removed {len(removable)} saved links."
            return True
        except Exception:
            st.session_state[save_error_key] = "Some links couldn’t be removed. Refresh your library and try again."
            return False

    def save_pasted_links() -> None:
        pasted, invalid = parse_sources(st.session_state.get(input_key, ""))
        st.session_state.pop(save_error_key, None)
        if not pasted or invalid:
            return
        new_links = [url for url in pasted if url not in saved_set]
        try:
            if new_links:
                save_links(init_db(), user_email, new_links)
            st.session_state[library_key]["urls"] = list(dict.fromkeys(saved + pasted))
            st.session_state[selection_key] = list(dict.fromkeys(
                st.session_state.get(selection_key, []) + pasted))
            st.session_state[input_key] = ""
            st.session_state[message_key] = (
                f"Saved {len(new_links)} new links. Your links are selected and ready to collect."
                if new_links else "These links are already saved. They’re selected and ready to collect."
            )
        except Exception:
            st.session_state[save_error_key] = (
                "Some links couldn’t be saved. Edit or re-enter the links to retry; saved links are kept."
            )

    with st.container(border=True, key=widget_key("link_workspace")):
        st.subheader("Your links")
        if platform != "facebook":
            st.caption("r/cambodia is included by default. Add other sources below; links are saved automatically.")
        if message_key in st.session_state:
            st.success(st.session_state.pop(message_key))
        if library["error"]:
            st.warning("Saved links couldn’t be loaded. Refresh to try again, or paste links below.")

        selected = st.multiselect(
            f"Sources ({len(saved)})", options=saved, key=selection_key,
            placeholder="Search and choose saved links", disabled=not saved,
        )
        st.session_state[memo_key] = list(selected)
        select_action, clear_action, refresh_action = st.columns(3)
        select_action.button(
            "Select all", key=widget_key("select_all_links"), use_container_width=True,
            disabled=not saved, on_click=select_all,
        )
        clear_action.button(
            "Clear", key=widget_key("clear_selected_links"), use_container_width=True,
            disabled=not selected and not st.session_state.get(input_key, "").strip(),
            help="Deselect all sources and clear pasted links. Saved links are kept.",
            on_click=clear_selection,
        )
        refresh_action.button(
            "Refresh", key=widget_key("refresh_saved_links"), use_container_width=True, on_click=refresh,
        )

        if platform == "facebook":
            with st.expander("Inspect all saved Facebook links"):
                render_facebook_link_list(library["urls"], key_prefix=f"facebook_link_list_{owner}",
                                         user_email=user_email if not library["error"] else None)

        removable = [url for url in selected if url != CAMBODIA_URL]
        if removable:
            if st.button(
                f"Remove {len(removable)} saved link{'s' if len(removable) != 1 else ''}",
                key=widget_key("remove_selected_links"), use_container_width=True,
                help="Removes these links from your library. Collected posts are kept.",
            ) and remove_selected():
                st.rerun()

        text = st.text_area(
            "Add links (one per line)", key=input_key, height=160,
            placeholder=("https://www.facebook.com/groups/your-community" if platform == "facebook" else
                         "https://www.reddit.com/r/cambodia/" if platform == "reddit" else
                         "https://www.facebook.com/groups/your-community\nhttps://www.reddit.com/r/cambodia/"),
            help="Paste as many links as you need. Valid links are saved and selected when you finish editing.",
            on_change=save_pasted_links,
        )
        if st.session_state.get(save_error_key):
            st.error(st.session_state[save_error_key])
        pasted, invalid = parse_sources(text)
        if invalid:
            st.error(
                f"Use valid {platform.title()} links in this tab, one per line." if platform else
                "Correct the invalid links before continuing. Use one http or https web link per line."
            )
            st.code("\n".join(invalid[:5]), language=None)

    targets = list(dict.fromkeys(selected + pasted))
    st.caption(f"{len(targets)} link{'s' if len(targets) != 1 else ''} ready to collect")
    return targets, not invalid


SENTIMENT_COLORS = {
    "Anxiety": "#f0ad4e",
    "Anger": "#d9534f",
    "Joy": "#5cb85c",
    "Neutral": "#5bc0de",
}


def _risk_color(score: Any) -> str:
    """Return a hex colour based on risk_score 1-10."""
    try:
        s = int(score)
    except (TypeError, ValueError):
        return "#888888"
    if s >= 8:
        return "#d9534f"
    if s >= 5:
        return "#f0ad4e"
    return "#5cb85c"


def _render_analysis_cards(analyses: List[Dict[str, Any]]) -> None:
    """Render model text as escaped text and show its source-level evidence."""
    for item in analyses:
        with st.container(border=True):
            monitor = item.get("monitor", {})
            if monitor:
                signal = monitor.get("signal_type", "unclear")
                st.subheader(SIGNAL_LABELS.get(signal, "Needs review"))
                st.caption(
                    f"{item.get('source_type', 'post').replace('_', ' ').title()} · "
                    f"Confidence: {monitor.get('confidence', 'low')} · AI assessment, unreviewed"
                )
                st.text(monitor.get("summary", ""))
                target = ", ".join(monitor.get("target_names", [])) or "No named project established"
                st.caption(f"Target: {target} · {monitor.get('target_kind', 'unclear').replace('_', ' ')}")
                for evidence in monitor.get("evidence", []):
                    st.caption(f"{evidence['purpose'].title()} evidence · {evidence['source'].replace('_', ' ')}")
                    st.text(evidence["quote"])
                for note in monitor.get("validation_notes", []):
                    st.warning(note)
                scope = monitor.get("watch_terms", [])
                st.caption("Analysis scope: " + (", ".join(scope) if scope else "Thailand generally"))
            else:
                st.caption("Older analysis — run analysis again to detect Thailand and boycott signals.")
            st.caption(f"Sentiment: {item.get('sentiment', 'Unknown')} · Review priority: {item.get('risk_score', '?')}/10")
            with st.expander("Translation and original excerpt"):
                st.text(item.get("translation", ""))
                st.text(item.get("raw_text_preview", ""))
            url = str(item.get("url", ""))
            if url.startswith(("https://", "http://")):
                st.link_button("Open source", url, use_container_width=True)


def _render_signal_overview(analyses: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    assessed = [a for a in analyses if a.get("monitor")]
    if not assessed:
        return analyses
    st.caption("Counts cover only these collected records. Posts and comments are separate observations; "
               "they do not measure unique people or public opinion. Classifications need human review.")
    cols = st.columns(3)
    cols[0].metric("Records assessed", len(assessed))
    cols[1].metric("Thailand signals", sum(is_flagged(a) for a in assessed))
    cols[2].metric("Boycott calls", sum(a["monitor"].get("signal_type") == "boycott_call" for a in assessed))
    choice = st.selectbox("Show findings", ["All records", "Flagged signals", "Boycott calls", "Boycott reports",
                                         "Negative sentiment", "Reported tensions", "Needs review"], key=key)
    signal_filter = {"Boycott calls": "boycott_call", "Boycott reports": "boycott_report",
                     "Negative sentiment": "negative_sentiment", "Reported tensions": "tension", "Needs review": "unclear"}
    visible = [a for a in analyses if choice == "All records"
               or (choice == "Flagged signals" and is_flagged(a))
               or a.get("monitor", {}).get("signal_type") == signal_filter.get(choice, "__none__")]
    export = [{"url": a.get("url", ""), "source_type": a.get("source_type", ""),
               "created_at": a.get("created_at", ""), "signal": a.get("monitor", {}).get("signal_type", "not_assessed"),
               "relevance": a.get("monitor", {}).get("thailand_relevance", ""),
               "targets": ", ".join(a.get("monitor", {}).get("target_names", [])),
               "summary": a.get("monitor", {}).get("summary", ""),
               "confidence": a.get("monitor", {}).get("confidence", ""),
               "evidence": " | ".join(f"{e['source']}: {e['quote']}" for e in a.get("monitor", {}).get("evidence", []))}
              for a in visible]
    if export:
        # Quotes and titles are untrusted spreadsheet cells.
        safe = pd.DataFrame(export).map(lambda v: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
        st.download_button("Export findings CSV", safe.to_csv(index=False).encode("utf-8-sig"),
                           "crisisradar-findings.csv", "text/csv", key=f"{key}_export")
    else:
        st.info("No records match this filter.")
    return visible


def _render_client_report(items: List[Dict[str, Any]], key: str, report_date: str,
                          render_evidence: Callable[[], None]) -> None:
    """Show results in the client's appendix templates, with the record evidence alongside."""
    records = [report_record(item) for item in items]
    negatives = negative_records(records)
    st.html(REPORT_CSS)
    _render_report_downloads(records, key, report_date)
    sentiment_tab, cloud_tab, reports_tab, evidence_tab = st.tabs([
        "Comment Sentiment Analysis", "Word cloud", f"Negative Post Reports ({len(negatives)})",
        "Evidence by record",
    ])
    with sentiment_tab:
        st.html(sentiment_sheet(records, report_date))
    with cloud_tab:
        render_word_cloud(records, key)
    with reports_tab:
        if not negatives:
            st.info("No negative or flagged records in this set, so there are no Negative Post Reports.")
        else:
            choice = st.selectbox(
                "Report", list(range(len(negatives))), key=f"{key}_npr",
                format_func=lambda i: (f"{report_number(negatives[i])} · {platform_label(negatives[i])} "
                                       f"{content_type(negatives[i]).lower()} · priority "
                                       f"{negatives[i].get('risk_score', '?')}/10"),
            )
            st.html(npr_sheet(negatives[choice], records, report_date))
    with evidence_tab:
        render_evidence()


def _render_report_downloads(records: List[Dict[str, Any]], key: str, report_date: str) -> None:
    """HTML is ready at once; a PDF is printed on request and kept until the report changes."""
    document = report_document(records, report_date)
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    name = f"crisisradar-report-{report_date[:10] or 'undated'}"
    pdf_key = f"{key}_pdf"
    with st.container(horizontal=True, key=f"{key}_downloads"):
        st.download_button(
            "Download report (HTML)", document.encode("utf-8"), f"{name}.html", "text/html", key=f"{key}_download",
            help="Sentiment analysis and every Negative Post Report, ready to share or print.",
        )
        stored = st.session_state.get(pdf_key)
        if stored and stored[0] == digest:
            st.download_button("Download PDF", stored[1], f"{name}.pdf", "application/pdf",
                               key=f"{key}_pdf_download", type="primary")
        elif st.button("Create PDF", key=f"{key}_pdf_create",
                       help="A4 landscape: one page per template and per Negative Post Report."):
            try:
                with st.spinner("Creating PDF…"):
                    pdf = render_pdf(document, f"CrisisRadar report · {report_date[:10] or 'undated'}")
            except PDFExportError as exc:
                st.error(str(exc))
            else:
                st.session_state[pdf_key] = (digest, pdf)
                st.rerun()


def _render_processed_deletion(rows: List[Dict[str, Any]]) -> None:
    selection_key = "processed_delete_selection"
    pending_key = "processed_delete_pending"
    if st.session_state.pop("processed_delete_reset", False):
        st.session_state[selection_key] = []
    notice = st.session_state.pop("processed_delete_notice", None)
    if notice:
        st.success(f"Deleted {len(notice['deleted'])} processed record(s).")
        if notice["skipped"]:
            st.info(f"Kept {len(notice['skipped'])} record(s) that are no longer processed. Reload to see their current status.")
        if notice["missing"]:
            st.info(f"{len(notice['missing'])} record(s) had already been removed.")
    records = {str(row["_id"]): row for row in rows
               if row.get("_id") and row.get("status") == "processed"}
    if not records:
        st.session_state.pop(pending_key, None)
        return
    if selection_key in st.session_state:
        st.session_state[selection_key] = [doc_id for doc_id in st.session_state[selection_key]
                                           if doc_id in records]

    def label(doc_id: str) -> str:
        row = records[doc_id]
        title = str(row.get("custom_title") or row.get("raw_text") or row.get("url") or "Record")
        return f"{' '.join(title.split())[:90]} · {doc_id[:12]}"

    with st.expander("Delete processed records"):
        selected = st.multiselect("Select processed records to delete", options=list(records),
                                  format_func=label, key=selection_key)
        if st.session_state.get(pending_key) != selected:
            st.session_state.pop(pending_key, None)
        if st.button("Delete selected records", key="request_processed_delete", disabled=not selected):
            st.session_state[pending_key] = selected.copy()
        pending = st.session_state.get(pending_key)
        if pending:
            st.warning(f"Permanently delete {len(pending)} processed record(s) and their saved analyses "
                       "from the shared library? This affects all users and cannot be undone. "
                       "Sentiment timeline summaries are kept.")
            confirm, cancel = st.columns(2)
            if confirm.button(f"Permanently delete {len(pending)} record(s)", key="confirm_processed_delete",
                              type="primary", use_container_width=True):
                try:
                    with st.spinner("Deleting processed records…"):
                        result = delete_processed_posts(init_db(), pending)
                except Exception:
                    st.error("Deletion could not be confirmed. Reload processed records to check their status before retrying.")
                else:
                    purge_record_caches(st.session_state, result)
                    st.session_state.pop(pending_key, None)
                    st.session_state["processed_delete_reset"] = True
                    st.session_state["processed_delete_notice"] = result
                    st.rerun()
            if cancel.button("Cancel", key="cancel_processed_delete", use_container_width=True):
                st.session_state.pop(pending_key, None)
                st.rerun()


def _render_processed_records(rows: List[Dict[str, Any]]) -> None:
    """Show previously processed Firestore records with their analysis."""
    has_analysis = [r for r in rows if r.get("analysis")]
    if not has_analysis:
        st.info("No records have analysis data yet.")
        return

    def evidence() -> None:
        _render_processed_evidence(has_analysis)

    report_date = max((str(r.get("processed_at") or "") for r in has_analysis), default="") or _now_iso()
    _render_client_report(has_analysis, "saved_report", report_date, evidence)


def _render_processed_evidence(has_analysis: List[Dict[str, Any]]) -> None:
    summary_rows = []
    for r in has_analysis:
        a = r.get("analysis", {})
        summary_rows.append({
            "_id": r.get("_id", ""),
            "custom_title": r.get("custom_title", ""),
            "sentiment": a.get("sentiment", ""),
            "risk_score": a.get("risk_score", ""),
            "topics": ", ".join(a.get("topics", [])) if isinstance(a.get("topics"), list) else str(a.get("topics", "")),
            "translation": str(a.get("translation", ""))[:200],
            "processed_at": r.get("processed_at", ""),
        })
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    # Also render as cards
    card_data = []
    for r in has_analysis:
        a = r.get("analysis", {})
        card_data.append({
            **a,
            "source_type": r.get("source_type", ""),
            "created_at": r.get("created_at", ""),
            "sentiment": a.get("sentiment", "Unknown"),
            "risk_score": a.get("risk_score", "?"),
            "topics": ", ".join(a.get("topics", [])) if isinstance(a.get("topics"), list) else str(a.get("topics", "")),
            "translation": a.get("translation", ""),
            "raw_text_preview": str(r.get("raw_text", ""))[:200],
            "url": r.get("url", ""),
        })
    card_data = _render_signal_overview(card_data, "saved_signals_filter")
    _render_analysis_cards(card_data)


def _bridge_secrets_to_env() -> None:
    """On Streamlit Cloud, populate os.environ from st.secrets so downstream
    code that reads os.getenv() keeps working.  Silently skips when no
    secrets.toml exists (i.e. local development with .env)."""
    mapping = {
        "GEMINI_KEY": "GEMINI_KEY",
        "GEMINI_MODEL": "GEMINI_MODEL",
        "FIREBASE_PROJECT_ID": "FIREBASE_PROJECT_ID",
        "FIREBASE_COLLECTION": "FIREBASE_COLLECTION",
        "FIREBASE_CREDENTIALS": "FIREBASE_CREDENTIALS",
    }
    try:
        secrets = dict(st.secrets)
    except Exception:
        return
    for secret_key, env_key in mapping.items():
        if secret_key in secrets and not os.getenv(env_key):
            os.environ[env_key] = str(secrets[secret_key])


_UI_THEME = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap");

.stApp,
.stApp button,
.stApp input,
.stApp textarea,
.stApp select,
.stApp label,
.stApp p,
.stApp span:not([data-testid="stIconMaterial"]),
.stApp div,
.stApp h1,
.stApp h2,
.stApp h3,
.stApp h4,
.stApp h5,
.stApp h6,
.stApp li,
.stApp a,
.stApp summary {
  font-family: Outfit, sans-serif;
}

button[data-testid^="stBaseButton-"],
a[data-testid^="stBaseLinkButton-"],
[data-testid="stDownloadButton"] button,
[data-testid="stExpander"] summary,
button[data-baseweb="tab"],
button[role="tab"],
[data-baseweb="input"],
[data-baseweb="textarea"],
[data-baseweb="select"] > div,
[data-testid="stCheckbox"] label,
[data-testid="stRadio"] label,
[data-testid="stToggle"] label,
[data-testid="stSlider"] [role="slider"],
[data-testid="stFileUploaderDropzone"],
[data-testid="stMultiSelect"] [data-baseweb="tag"],
[data-baseweb="menu"] [role="option"],
a[href] {
  transition: transform 180ms cubic-bezier(0.22, 1, 0.36, 1), box-shadow 180ms ease, background-color 180ms ease, border-color 180ms ease, color 180ms ease, filter 180ms ease, opacity 180ms ease;
}

button[data-testid^="stBaseButton-"]:hover:not(:disabled),
a[data-testid^="stBaseLinkButton-"]:hover,
[data-testid="stDownloadButton"] button:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 18px rgba(38, 39, 48, 0.14);
  filter: brightness(1.04);
}

button[data-testid^="stBaseButton-"]:active:not(:disabled),
a[data-testid^="stBaseLinkButton-"]:active,
[data-testid="stDownloadButton"] button:active {
  transform: translateY(0) scale(0.98);
  box-shadow: 0 2px 6px rgba(38, 39, 48, 0.1);
}

button[data-testid="stBaseButton-header"]:hover:not(:disabled),
button[data-testid="stBaseButton-headerNoPadding"]:hover:not(:disabled),
button[data-testid="stBaseButton-minimal"]:hover:not(:disabled),
button[data-testid="stBaseButton-borderlessIcon"]:hover:not(:disabled),
button[data-testid="stBaseButton-borderlessIconActive"]:hover:not(:disabled) {
  transform: scale(1.08);
  box-shadow: none;
  filter: none;
  background-color: rgba(74, 144, 217, 0.12);
}

button[data-baseweb="tab"]:hover,
button[role="tab"]:hover,
[data-testid="stExpander"] summary:hover {
  transform: none;
  box-shadow: none;
  background-color: rgba(74, 144, 217, 0.1);
  color: #4A90D9;
}

[data-baseweb="input"]:hover,
[data-baseweb="textarea"]:hover,
[data-baseweb="select"]:hover > div,
[data-testid="stFileUploaderDropzone"]:hover {
  border-color: #4A90D9 !important;
  box-shadow: 0 0 0 3px rgba(74, 144, 217, 0.16);
}

[data-testid="stCheckbox"]:hover label,
[data-testid="stRadio"]:hover label,
[data-testid="stToggle"]:hover label,
[data-testid="stMultiSelect"] [data-baseweb="tag"]:hover,
[data-baseweb="menu"] [role="option"]:hover {
  color: #4A90D9;
}

[data-testid="stSlider"]:hover [role="slider"] {
  transform: scale(1.16);
  box-shadow: 0 0 0 6px rgba(74, 144, 217, 0.18);
}

a[href]:hover { opacity: 0.78; }

/* Comfortable tap targets and a persistent Facebook connection card. */
.stButton > button, .stLinkButton > a { min-height: 48px; border-radius: 12px; font-weight: 600; }

/* Destructive actions beside individual records in Clean & manage data. */
.stApp :is([class*="st-key-record_manager_delete_"], [class*="st-key-record_manager_row_confirm_"]) button {
  background: #dc2626;
  border-color: #dc2626;
  color: #fff;
}
.stApp :is([class*="st-key-record_manager_delete_"], [class*="st-key-record_manager_row_confirm_"]) button:hover:not(:disabled),
.stApp :is([class*="st-key-record_manager_delete_"], [class*="st-key-record_manager_row_confirm_"]) button:active:not(:disabled) {
  background: #b91c1c;
  border-color: #b91c1c;
  color: #fff;
}
.stApp :is([class*="st-key-record_manager_delete_"], [class*="st-key-record_manager_row_confirm_"]) button:focus-visible {
  outline: 3px solid #ef4444;
  outline-offset: 3px;
}

button[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapseButton"] button { min-width: 48px; min-height: 48px; }
[data-baseweb="tab"] { min-height: 48px; padding-inline: 20px; }
.stTextInput input, .stTextArea textarea { font-size: 16px; }
.st-key-facebook_connection { border-radius: 18px; }
.fb-identity { display: flex; align-items: center; gap: 14px; padding: 4px 0; }
.fb-avatar { display: grid; place-items: center; flex: 0 0 48px; height: 48px;
  border-radius: 14px; background: #edf3ff; color: #165dcc; font-size: 20px; font-weight: 700; }
.st-key-facebook_connection .fb-label { margin: 0 0 3px; font-size: 12px; font-weight: 700; letter-spacing: .08em; color: #56657a; }
.st-key-facebook_connection .fb-name { margin: 0; font-size: 18px; font-weight: 650; line-height: 1.4; overflow-wrap: anywhere; }
.st-key-facebook_connection .fb-detail { margin: 4px 0 0; color: #56657a; font-size: 14px; }
.fb-status { display: inline-flex; align-items: center; gap: 8px; padding: 9px 14px;
  border-radius: 999px; background: #eaf7ef; color: #17633b; font-size: 14px; font-weight: 600; }
.fb-status::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: #25804d; }
@media (max-width: 640px) {
  [data-baseweb="tab"] { padding-inline: 14px; }
  .block-container { padding-inline: 20px; }
}

/* Step modals: darken and blur the workspace behind the dialog so one step holds attention. */
div[data-testid="stDialog"] {
  background-color: rgba(9, 13, 20, 0.58) !important;
  backdrop-filter: blur(7px) saturate(85%);
  -webkit-backdrop-filter: blur(7px) saturate(85%);
}
div[data-testid="stDialog"] [role="dialog"] {
  box-shadow: 0 26px 64px rgba(8, 12, 20, 0.45);
}

/* A brief entrance when content appears; stable widgets keep their DOM nodes.
   Backwards fill releases the animation after it finishes, so hover effects
   and positioned children retain their normal behavior. */
@keyframes sm-load-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes sm-load-rise {
  from { opacity: 0; translate: 0 12px; }
  to { opacity: 1; translate: 0 0; }
}
@media (prefers-reduced-motion: no-preference) {
  .st-key-sm_views,
  html.sm-navigation-ready .sm-sidebar {
    animation: sm-load-fade 320ms cubic-bezier(0.22, 1, 0.36, 1) backwards;
  }

  .st-key-auth_gateway,
  .st-key-sm_source_card_facebook,
  .st-key-sm_source_card_x,
  .st-key-sm_source_card_news,
  .st-key-sm_source_card_website {
    /* Individual translate keeps the existing hover transform independent. */
    animation: sm-load-rise 460ms cubic-bezier(0.22, 1, 0.36, 1) var(--sm-load-delay, 0ms) backwards;
  }
  .st-key-sm_source_card_facebook { --sm-load-delay: 40ms; }
  .st-key-sm_source_card_x { --sm-load-delay: 80ms; }
  .st-key-sm_source_card_news { --sm-load-delay: 120ms; }
  .st-key-sm_source_card_website { --sm-load-delay: 160ms; }

  /* Keyboard users can interact immediately, including during the stagger. */
  .st-key-sm_views:focus-within,
  html.sm-navigation-ready .sm-sidebar:focus-within,
  .st-key-auth_gateway:focus-within,
  .st-key-sm_source_card_facebook:focus-within,
  .st-key-sm_source_card_x:focus-within,
  .st-key-sm_source_card_news:focus-within,
  .st-key-sm_source_card_website:focus-within {
    opacity: 1 !important;
    translate: none !important;
  }
}
@media (prefers-reduced-motion: reduce) {
  div[data-testid="stDialog"] { backdrop-filter: none; -webkit-backdrop-filter: none; }
  .stApp button,
  .stApp a,
  .stApp summary,
  .stApp [data-baseweb="input"],
  .stApp [data-baseweb="select"],
  .stApp [role="slider"] {
    transition: none !important;
    transform: none !important;
  }
}
</style>
"""


def _apply_ui_theme() -> None:
    st.markdown(_UI_THEME, unsafe_allow_html=True)
    st.html(Path(__file__).parent / "modules" / "theme" / "button-shimmer.css")
    apply_brand()
    install_audio_feedback()


def _render_reddit_settings() -> tuple[int, int]:
    st.subheader("Reddit · r/cambodia")
    st.caption("Collect posts and comments directly from r/cambodia.")
    with st.container(border=True):
        if reddit_configured():
            st.caption("API credentials configured. Access is checked when collecting.")
        else:
            st.info("Reddit is not connected yet. An administrator needs to configure approved Reddit API access.")
        rc1, rc2 = st.columns(2)
        limit = rc1.number_input("Reddit posts per source", min_value=1, max_value=500,
                                 value=50, step=25, key="reddit_post_limit")
        comments = rc2.number_input("Comments per post", min_value=0, max_value=100,
                                    value=20, step=10, key="reddit_comment_limit")
        st.caption("Collects newest posts by default and samples the newest returned comments. "
                   "This is not a complete subreddit archive. Large runs may return partial results.")
    return int(limit), int(comments)


def _render_cloud_save(records: List[Dict[str, Any]], save_key: str, noun: str = "posts",
                       *, source: str = "Facebook") -> None:
    recent = st.session_state.get("last_collected_dataset")
    titles = st.session_state.setdefault("collection_titles", {})
    identity = _collection_record_keys(records)
    saved_title = titles.get(save_key, {})
    if saved_title.get("identity") != identity:
        saved_title = {"identity": identity, "ids": [get_post_id(str(row["url"])) for row in records
                                                   if row.get("url") and row.get("source_type") not in NON_CONTENT_TYPES],
                       "title": standard_dataset_title(source)}
        titles[save_key] = saved_title
    if isinstance(recent, dict) and recent.get("title") and identity == _collection_record_keys(recent.get("rows", [])):
        saved_title["title"] = recent["title"]
    st.caption(f"Dataset: {saved_title['title']} · Rename it in Clean & manage data.")
    if st.button("Save data to cloud", key=save_key):
        try:
            write_summary = _write_results_to_firestore(records, custom_title=saved_title["title"])
            confirmation = (
                "Saved to Firestore: "
                f"{write_summary['inserted']} {noun} "
                f"(skipped: {write_summary['skipped']})."
            )
            if _focus_modal_open("facebook"):
                st.session_state["facebook_save_confirmation"] = confirmation
                _close_focus_modal()
                st.rerun()
            else:
                st.success(confirmation)
        except Exception as exc:
            error_text = str(exc).strip() or repr(exc)
            st.error(f"Firestore write failed: {error_text}")


def _autosave_collection(collection: dict[str, Any], source: str, custom_title: str = "") -> None:
    """Attempt once per collection (or explicit retry), retaining results on failure."""
    save = collection.setdefault("cloud_save", {
        "title": custom_title.strip() or standard_dataset_title(source),
        "state": "pending", "count": 0,
    })
    # Removed X records must be saved too, to clear their previous text/analysis.
    records = list({str(row.get("url") or "").strip(): row for row in collection.get("rows", [])
                    if str(row.get("url") or "").strip() and row.get("source_type") not in NON_CONTENT_TYPES}.values())
    if not records:
        save.update(state="empty", count=0)
        return
    updated_ids = {get_post_id(str(row["url"])) for row in records}
    cached_rows = [row for key in ("firestore_rows", "processed_rows") for row in st.session_state.get(key, [])]
    cached_rows += (st.session_state.get("analysis_last_results") or {}).get("analyses", [])
    if any(str(row.get("_id")) in updated_ids for row in cached_rows):
        # Reusing a title must not leave old source text runnable after an edit.
        _clear_loaded_analysis()
    try:
        with running_glow(), st.spinner("Saving collected records to the cloud…"):
            summary = _write_results_to_firestore(records, custom_title=save["title"])
    except Exception:
        save.update(state="error", count=0)
        # A failed write can still have saved some rows. Refresh cloud views.
        st.session_state.pop("analysis_dataset_catalog", None)
        st.session_state.pop("record_manager_rows", None)
        st.session_state.pop("record_manager_pending", None)
    else:
        save.update(state="saved", count=summary["inserted"])


def _collection_save_pending(key: str) -> bool:
    collection = st.session_state.get(key) or {}
    return ((collection.get("cloud_save") or {}).get("state") == "error"
            and bool(_collection_record_keys(collection.get("rows", []))))


def _render_autosave_status(collection: dict[str, Any], retry_key: str,
                            on_saved: Callable[[], None] | None = None) -> None:
    if not _collection_record_keys(collection.get("rows", [])):
        return
    save = collection.get("cloud_save") or {}
    if save.get("state") == "saved":
        st.success(f"Saved {save['count']} record(s) to the cloud.")
        st.caption(f"Dataset: {save['title']} · Available in Sentiment Analysis and Clean & manage data.")
    elif save.get("state") == "error":
        st.error("Cloud saving failed. Some records may already be saved. Your collected data is still in this session; "
                 "keep this page open and retry saving before collecting again.")
        st.caption(f"Dataset: {save['title']} · Retrying saves these results without scraping again.")
        if st.button("Retry saving collected records", key=retry_key):
            _autosave_collection(collection, source="")  # Reuses the frozen dataset title and collected rows.
            if save.get("state") == "saved" and on_saved:
                on_saved()
            st.rerun()


def _show_collection_progress(completed: int, total: int, activity: dict[str, tuple[str, int]],
                              started_at: float, *, posts: int = 0, stopping: bool = False) -> None:
    elapsed = max(0, int(time.time() - started_at))
    duration = f"{elapsed // 60}m {elapsed % 60:02d}s" if elapsed >= 60 else f"{elapsed}s"
    posts = max(posts, sum(count for _message, count in activity.values()))
    status = "Stopping collection" if stopping else "Collection running"
    st.progress(completed / max(total, 1), text=(
        f"{status} · {completed} of {total} sources finished · {posts} posts collected · {duration} elapsed"))
    for url, (message, _count) in activity.items():
        if message != "Source finished":
            st.caption(f"{message} · {url}")


@st.fragment(run_every=2)
def _render_facebook_collection_progress(email: str, job_id: str) -> None:
    from modules.auth import allowed_emails, verify_access
    try:
        if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != email:
            raise PermissionError()
    except Exception:
        cancel_collection(email, job_id)
        st.rerun(scope="app")
    job = collection_job(email, job_id)
    if job is None or job.status not in {"running", "cancelling"}:
        st.session_state.pop("facebook_collection_job", None)
        if job:
            st.session_state["last_scrape_results"] = job.results
            _remember_collection(job.results, "Facebook", job.finished_at)
            if job.error:
                notify_sound("error")
            st.session_state["last_scrape_urls"] = list(job.urls)
            st.session_state["last_scrape_error"] = job.error or (
                "Collection cancelled. Completed results have been kept." if job.status == "cancelled" else "")
        else:
            st.session_state["last_scrape_error"] = "The collection ended when the app restarted. Please try again."
        st.rerun(scope="app")
    total = len(job.urls)
    show_running_glow()
    _show_collection_progress(job.completed, total, job.activity, job.started_at,
                              posts=sum(row.get("source_type") not in NON_CONTENT_TYPES for row in job.results),
                              stopping=job.status == "cancelling")
    if job.status == "cancelling":
        st.info("Stopping collection and releasing your Facebook connection…")
    else:
        st.caption("You can switch workspaces. Another click or browser tab will keep using this collection.")
    if st.button("Cancel collection", key="cancel_facebook_collection", disabled=job.status == "cancelling"):
        cancel_collection(email, job_id)


FACEBOOK_STEP_KEY = "facebook_wizard_step"
FACEBOOK_TARGETS_KEY = "facebook_wizard_targets"
FACEBOOK_LINKS_VALID_KEY = "facebook_wizard_links_valid"
FACEBOOK_SEARCH_STEP_KEY = "facebook_search_wizard_step"


def _remember_facebook_search_settings() -> None:
    settings = st.session_state.setdefault("facebook_search_settings", {})
    for field in ("url", "scroll_times"):
        key = f"facebook_search_{field}"
        if key in st.session_state:
            settings[field] = st.session_state[key]


def _facebook_ready(session: FacebookSession) -> bool:
    """A hosted collection needs a confirmed connection; a local one reuses a saved session."""
    return session.status == "connected" if IS_CLOUD else session.status in {"connected", "saved"}


def _auto_check_facebook(user_email: str, session: FacebookSession) -> FacebookSession:
    """Confirm a saved hosted login once per visit so a working connection stays out of the way."""
    if not IS_CLOUD or session.status != "saved":
        return session
    if st.session_state.get("facebook_login_job") or st.session_state.get("facebook_auto_check_started"):
        return session
    st.session_state["facebook_auto_check_started"] = True
    from modules.auth import allowed_emails, verify_access
    try:
        if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != user_email:
            return session
    except Exception:
        return session  # main() will rerun the authorization gate before rendering.
    try:
        st.session_state["facebook_login_job"] = start_login(user_email, check_only=True)
    except Exception:
        logging.getLogger(__name__).exception("Automatic Facebook connection check could not start")
    return session


def _render_step_header(labels: List[str], index: int) -> None:
    for column, (position, label) in zip(st.columns(len(labels)), enumerate(labels)):
        if position < index:
            column.caption(f"✓ {label}")
        elif position == index:
            column.markdown(f"**Step {position + 1} · {label}**")
        else:
            column.caption(f"{position + 1}. {label}")


FOCUS_MODAL_KEY = "focus_workspace_modal"


def _open_focus_modal(name: str) -> None:
    st.session_state[FOCUS_MODAL_KEY] = name
    st.session_state[f"{name}_wizard_visited"] = True
    st.session_state["x_focus_open"] = False  # Streamlit allows one open dialog per run.


def _close_focus_modal() -> None:
    st.session_state.pop(FOCUS_MODAL_KEY, None)


def _focus_modal_open(name: str) -> bool:
    # Only one workspace shows its steps at a time; Streamlit allows one open dialog per run,
    # so the X workspace's own dialog takes precedence over these.
    if st.session_state.get("x_focus_open"):
        return False
    return st.session_state.get(FOCUS_MODAL_KEY) == name


def _render_focus_trigger(name: str, labels: List[str], index: int, start_label: str) -> None:
    started = bool(st.session_state.get(f"{name}_wizard_visited"))
    label = (f"Continue · step {index + 1} of {len(labels)}: {labels[index]}" if started else start_label)
    st.button(label, key=f"open_{name}_steps", type="primary", use_container_width=True,
              help="Opens the guided steps in a focused window.",
              on_click=_open_focus_modal, args=(name,))


def _go_to_step(step_key: str, label: str) -> None:
    st.session_state[step_key] = label


def _render_step_nav(step_key: str, labels: List[str], index: int, can_continue: bool) -> None:
    # A callback keeps the step change in one script run, so the hidden step leaves no stale widgets.
    previous, following = st.columns(2)
    previous.button("← Previous", key=f"{step_key}_previous", use_container_width=True,
                    disabled=index == 0, on_click=_go_to_step,
                    args=(step_key, labels[max(index - 1, 0)]))
    following.button("Next →", key=f"{step_key}_next", use_container_width=True,
                     disabled=index >= len(labels) - 1 or not can_continue, on_click=_go_to_step,
                     args=(step_key, labels[min(index + 1, len(labels) - 1)]))


def _render_collection_workspace(
    platform: str, user_email: str, facebook_session: FacebookSession,
) -> None:
    if platform == "facebook":
        _render_facebook_workspace(user_email, facebook_session)
        return
    reddit_limit, reddit_comments = _render_reddit_settings()
    _render_collect_controls(
        "reddit", user_email, [CAMBODIA_URL], True, facebook_session,
        use_headless=True, scroll_times=10,
        reddit_limit=reddit_limit, reddit_comments=reddit_comments,
    )


def _render_facebook_workspace(user_email: str, session: FacebookSession) -> None:
    """Open the guided steps in a focused modal: connect (only when needed), links, then collect."""
    search = st.session_state.get("scrape_source") == "facebook_search"
    if st.session_state.get("scrape_source") in {"facebook", "facebook_search"} or _focus_modal_open("facebook"):
        session = _auto_check_facebook(user_email, session)
    labels = ([] if _facebook_ready(session) else ["Connect Facebook"]) + (
        ["Collect posts"] if search else ["Your links", "Collect posts"])
    step_key = FACEBOOK_SEARCH_STEP_KEY if search else FACEBOOK_STEP_KEY
    active_job = active_collection(user_email) if IS_CLOUD else None
    if active_job:
        st.session_state["facebook_collection_job"] = active_job.id
    # A running or just-finished hosted collection belongs to the final step.
    collecting = IS_CLOUD and (bool(active_job) or bool(st.session_state.get("facebook_collection_job")))
    current = st.session_state.get(step_key, labels[0])
    if collecting or current not in labels:
        current = labels[-1] if collecting else labels[0]
    st.session_state[step_key] = current
    index = labels.index(current)
    if _focus_modal_open("facebook"):
        _facebook_steps_modal(user_email, session, labels, index, search=search)
        return
    with st.container(border=True, key="facebook_overview"):
        st.subheader("Oyoshi on Facebook" if search else "Facebook", help=None if search else FACEBOOK_USAGE_WARNING)
        st.caption("Open Oyoshi search results directly and collect posts with 2–5 scrolls." if search else
                   "Collect posts and comments from the Facebook groups and pages you choose.")
        save_confirmation = st.session_state.pop("facebook_save_confirmation", None)
        if save_confirmation:
            st.success(save_confirmation)
        if _facebook_ready(session):
            st.caption(f"Connection ready{' · ' + session.name if session.name else ''}.")
        else:
            st.caption("Not connected yet. The first step signs you in to Facebook.")
        if not search:
            selected = st.session_state.get(FACEBOOK_TARGETS_KEY, [])
            st.caption(f"{len(selected)} link{'s' if len(selected) != 1 else ''} selected.")
        if collecting:
            _render_facebook_collection_progress(user_email, st.session_state["facebook_collection_job"])
        _render_focus_trigger("facebook", labels, index, "Start collecting Facebook posts")


@st.dialog("Collect Facebook posts", width="large", on_dismiss=_close_focus_modal)
def _facebook_steps_modal(user_email: str, session: FacebookSession,
                          labels: List[str], index: int, *, search: bool = False) -> None:
    # Dialog widgets rerun independently of main(), including its sign-in gate.
    if user_email:
        from modules.auth import allowed_emails, verify_access
        try:
            if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != user_email:
                raise PermissionError()
        except Exception:
            _close_focus_modal()
            st.rerun()
    # Dialog reruns retain their original call arguments. Read the live step so
    # Next and Previous navigation take effect immediately.
    step_key = FACEBOOK_SEARCH_STEP_KEY if search else FACEBOOK_STEP_KEY
    current = st.session_state.get(step_key, labels[index] if index < len(labels) else labels[0])
    if current not in labels:
        current = labels[0]
    index = labels.index(current)
    st.subheader("Oyoshi on Facebook" if search else "Facebook", help=None if search else FACEBOOK_USAGE_WARNING)
    _render_step_header(labels, index)

    if current == "Connect Facebook":
        session = _render_facebook_connection(session, user_email=user_email)
        can_continue = _facebook_ready(session)
        if can_continue:
            st.success("Facebook is connected. Continue to collect Oyoshi search results." if search else
                       "Facebook is connected. Continue to choose your links.")
    elif current == "Your links":
        targets, links_valid = _render_link_workspace(user_email, platform="facebook")
        st.session_state[FACEBOOK_TARGETS_KEY] = targets
        st.session_state[FACEBOOK_LINKS_VALID_KEY] = links_valid
        can_continue = links_valid and bool(targets)
        if links_valid and not targets:
            st.caption("Select a saved link or add one above to continue.")
    else:
        if search:
            saved_settings = st.session_state.get("facebook_search_settings", {})
            raw_url = st.text_input("Oyoshi Facebook search URL", value=saved_settings.get("url", OYOSHI_SEARCH_URL),
                                    key="facebook_search_url", on_change=_remember_facebook_search_settings)
            try:
                targets, links_valid = [validate_facebook_search_url(raw_url)], True
            except ValueError as exc:
                targets, links_valid = [], False
                st.error(str(exc))
            if links_valid:
                st.link_button("Open this search on Facebook", targets[0])
            st.caption("The collector opens this search link directly. Choose between 2 and 5 scroll iterations below.")
        else:
            targets = list(st.session_state.get(FACEBOOK_TARGETS_KEY, []))
            links_valid = bool(st.session_state.get(FACEBOOK_LINKS_VALID_KEY, True))
            st.caption(f"Collecting from {len(targets)} selected link{'s' if len(targets) != 1 else ''}. "
                       "Go back to change them.")
        if not _facebook_ready(session):
            st.info("Your Facebook connection needs attention. Go back to the first step to reconnect.")
        settings = st.columns(2)
        use_headless = settings[0].checkbox("Headless Browser", value=True, disabled=IS_CLOUD,
                                            key="facebook_search_headless" if search else "facebook_headless")
        if search:
            scroll_times = settings[1].slider("Scroll Iterations", min_value=2, max_value=5,
                                              value=saved_settings.get("scroll_times", 3),
                                              key="facebook_search_scroll_times",
                                              on_change=_remember_facebook_search_settings)
        else:
            scroll_times = settings[1].slider("Scroll Iterations", min_value=1, max_value=50, value=10,
                                              key="facebook_scroll_times")
        _render_collect_controls(
            "facebook", user_email, targets, links_valid, session,
            use_headless=use_headless, scroll_times=scroll_times,
            reddit_limit=50, reddit_comments=20,
            strict_scroll_limit=search,
            previous_step=(step_key, labels[index - 1]) if index > 0 else None,
        )
        return  # The final navigation action is Collect posts.
    _render_step_nav(step_key, labels, index, can_continue)


def _render_collect_controls(
    platform: str, user_email: str, target_urls: List[str], links_valid: bool,
    facebook_session: FacebookSession, *, use_headless: bool, scroll_times: int,
    reddit_limit: int, reddit_comments: int,
    strict_scroll_limit: bool = False,
    previous_step: tuple[str, str] | None = None,
) -> None:
    # Keep each source's results and save controls independent when switching sources.
    result_key = "last_scrape" if platform == "facebook" else "last_reddit_scrape"
    save_key = "save_last_results_btn" if platform == "facebook" else "save_reddit_results_btn"
    hosted_facebook = platform == "facebook" and IS_CLOUD
    active_job = active_collection(user_email) if hosted_facebook else None
    if active_job:
        st.session_state["facebook_collection_job"] = active_job.id
    collection_id = st.session_state.get("facebook_collection_job") if hosted_facebook else None
    if collection_id:
        _render_facebook_collection_progress(user_email, collection_id)
    action = st.container()
    posts = [row for row in (st.session_state.get(f"{result_key}_results") or [])
             if row.get("source_type") not in NON_CONTENT_TYPES]
    if previous_step and (not posts or active_job):
        previous, action = action.columns(2)
        previous.button("← Previous", key=f"{previous_step[0]}_previous", use_container_width=True,
                        disabled=bool(active_job), on_click=_go_to_step, args=previous_step)
    if action.button(
        "Collecting posts…" if active_job else "Collect posts",
        key=f"collect_{platform}_posts", type="primary", use_container_width=True,
        disabled=not links_valid or not target_urls or (platform == "facebook" and IS_CLOUD and (
            facebook_session.status != "connected" or bool(st.session_state.get("facebook_login_job")) or bool(active_job))),
    ):
        if not links_valid:
            st.error("Correct the invalid links above before collecting posts.")
        elif not target_urls:
            st.error("Please provide at least one URL.")
        else:
            st.session_state[f"{result_key}_urls"] = target_urls
            st.session_state[f"{result_key}_error"] = ""
            # Remove warnings from the previous attempt as soon as a new run starts.
            st.session_state[f"{result_key}_results"] = None
            if hosted_facebook:
                async def run(progress, partial, activity):
                    return await _run_multi_scrape_async(
                        urls=list(target_urls), headless=True, scroll_times=scroll_times,
                        facebook_owner=user_email, on_progress=progress, on_results=partial,
                        strict_scroll_limit=strict_scroll_limit, on_activity=activity,
                    )
                st.session_state["facebook_collection_job"] = start_collection(user_email, target_urls, run)
                st.rerun()
            progress_area = st.empty()
            activity: dict[str, tuple[str, int]] = {}
            completed = 0
            started_at = time.time()

            def render_progress() -> None:
                with progress_area.container():
                    _show_collection_progress(completed, len(target_urls), activity, started_at)

            def update_activity(url: str, message: str, posts: int) -> None:
                activity[url] = (message, posts)
                render_progress()

            def update_progress(done: int, total: int, url: str) -> None:
                nonlocal completed
                completed = done
                render_progress()

            render_progress()
            with running_glow(), st.spinner("Running scraper..."):
                try:
                    results = _run_multi_scrape_sync(
                        urls=target_urls,
                        headless=use_headless,
                        scroll_times=scroll_times,
                        reddit_limit=int(reddit_limit), reddit_comments=int(reddit_comments),
                        facebook_owner=user_email if platform == "facebook" and IS_CLOUD else None,
                        strict_scroll_limit=strict_scroll_limit,
                        on_progress=update_progress, on_activity=update_activity,
                    )
                    st.session_state[f"{result_key}_results"] = results
                    _remember_collection(results, platform.title())
                    progress_area.empty()
                    if platform == "facebook":
                        # Refresh the action row and connection status after collection.
                        st.rerun()
                    else:
                        st.session_state.pop("record_manager_rows", None)
                except Exception as exc:
                    progress_area.empty()
                    st.session_state[f"{result_key}_results"] = []
                    _remember_collection([], platform.title())
                    error_text = str(exc).strip() or repr(exc)
                    st.session_state[f"{result_key}_error"] = error_text
                    st.error(f"Scrape failed: {error_text}")

    if active_job:
        return  # Previous results/errors are not the status of the running job.
    scrape_results = st.session_state.get(f"{result_key}_results", None)
    scrape_urls = st.session_state.get(f"{result_key}_urls", [])
    scrape_error = st.session_state.get(f"{result_key}_error", "")
    if scrape_results is not None:
        if scrape_urls:
            with st.expander(f"Sources in the last collection ({len(scrape_urls)})"):
                st.code("\n".join(scrape_urls))
        if scrape_error:
            st.caption(f"Last error: {scrape_error}")
        issues = [r for r in scrape_results if r.get("source_type") in NON_CONTENT_TYPES]
        for issue in issues:
            st.warning(f"{issue.get('target_url', issue.get('url', 'Source'))}: {issue.get('raw_text', 'Collection incomplete')}")
        scrape_results = [r for r in scrape_results if r.get("source_type") not in NON_CONTENT_TYPES]
        st.write(f"**Records collected:** {len(scrape_results)}")
        if scrape_results:
            _render_cloud_save(scrape_results, save_key, source=platform.title())
            st.dataframe(pd.DataFrame(scrape_results), use_container_width=True, hide_index=True)
            with st.expander("Raw JSON Output"):
                st.json(scrape_results)
        else:
            st.info("No source records were collected. Check any connection notices above.")


@st.cache_data(ttl=60, show_spinner=False)
def _load_news_history() -> list[dict[str, Any]]:
    return load_news_history(init_db())


def _risk_metric(column: Any, label: str, snapshot: Dict[str, Any]) -> None:
    delta = snapshot.get("risk_delta")
    column.metric(label, f"{snapshot['risk_score']:.0f} · {snapshot['risk_level']}",
                  None if delta is None else f"{delta:+.1f} vs previous scan",
                  delta_color="off" if not delta else "inverse")


def _render_risk_alerts(snapshot: Dict[str, Any], scope: str) -> None:
    risk, level, delta = snapshot["risk_score"], snapshot["risk_level"], snapshot.get("risk_delta")
    if snapshot.get("rising"):
        st.error(f"Border tension risk is rising: {risk:.0f}/100 ({level}), up {delta:.1f} points since the previous scan.")
    elif level == "High":
        st.error(f"Border tension risk is high in {scope}: {risk:.0f}/100.")
    elif level == "Elevated":
        st.warning(f"Border tension risk is elevated in {scope}: {risk:.0f}/100.")
    if snapshot.get("boycott_count"):
        count = snapshot["boycott_count"]
        st.warning(f"{count} article{'s' if count != 1 else ''} in {scope} mention{'s' if count == 1 else ''} "
                   "a boycott linked to Thailand or Cambodia.")


def _news_table(articles: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for article in articles:
        assessment = article.get("news_assessment") or {}
        categories = article.get("news_categories") or [article.get("news_category", "")]
        rows.append({
            "Published": pd.to_datetime(article.get("created_at") or None, utc=True),
            "Flags": ", ".join(assessment.get("flags", [])) or "—",
            "Headline": article.get("title") or article.get("raw_text", ""),
            "Publisher": article.get("publisher", ""),
            "Category": ", ".join(NEWS_CATEGORIES.get(c, {}).get("label", c) for c in categories if c),
            "Tone": assessment.get("tone", ""),
            "Matched terms": ", ".join(assessment.get("matched_terms", [])),
            "Link": article.get("url", ""),
        })
    return pd.DataFrame(rows)


def _render_news_workspace() -> None:
    st.subheader("Online news · Google News")
    st.caption("Searches Google News, the News tab of Google Search, for each watch category and scans every "
               "headline for boycotts and Thailand–Cambodia border tension.")
    with st.container(border=True, key="news_settings"):
        searches: Dict[str, str] = {}
        for key, category in NEWS_CATEGORIES.items():
            toggle, terms = st.columns([2, 3], vertical_alignment="center")
            enabled = toggle.checkbox(category["label"], value=True, key=f"news_enabled_{key}")
            query = terms.text_input(f"{category['label']} search terms", value=category["query"],
                                     key=f"news_query_{key}", label_visibility="collapsed", disabled=not enabled)
            if enabled and query.strip():
                searches[key] = query.strip()
        window_col, edition_col, limit_col = st.columns(3)
        window = window_col.selectbox("Published", list(TIME_WINDOWS), index=list(TIME_WINDOWS).index("30d"),
                                      format_func=TIME_WINDOWS.get, key="news_window")
        editions = edition_col.multiselect("Google News editions", list(EDITIONS), default=list(EDITIONS),
                                           format_func=lambda e: EDITIONS[e]["label"], key="news_editions")
        limit = limit_col.number_input("Articles per search", min_value=10, max_value=MAX_ARTICLES,
                                       value=50, step=10, key="news_limit")

    if st.button("Search Google News", key="collect_news_articles", type="primary", use_container_width=True,
                 disabled=_collection_save_pending("news_scan")) and not _collection_save_pending("news_scan"):
        if not searches:
            st.error("Choose at least one category with search terms.")
        elif not editions:
            st.error("Choose at least one Google News edition.")
        else:
            progress = st.progress(0, text="Searching Google News…")
            with running_glow(), st.spinner("Scanning news coverage…"):
                rows = collect_news(searches, window=window, editions=editions, limit=int(limit),
                                    on_progress=lambda done, total, query: progress.progress(
                                        done / total, text=f"Searched {done} of {total}: {query}"))
            _remember_collection(rows, "Online news")
            st.session_state["news_scan"] = {"rows": rows, "snapshot": None, "saved": False,
                                             "searches": searches, "window": window, "editions": editions}
            _autosave_collection(st.session_state["news_scan"], "Online news")
            try:
                history = _load_news_history()
            except Exception:
                history = []
            snapshot = build_scan_snapshot(rows, searches=searches, window=window, editions=editions,
                                           previous=history[-1] if history else None)
            saved = False
            if snapshot:
                try:
                    persist_news_scan(init_db(), snapshot)
                    saved = True
                except Exception:
                    logging.getLogger(__name__).exception("News scan snapshot could not be saved")
                _load_news_history.clear()
            st.session_state["news_scan"].update(snapshot=snapshot, saved=saved)
            st.rerun()

    scan = st.session_state.get("news_scan")
    if not scan:
        return
    _render_autosave_status(scan, "retry_news_save")
    for issue in (r for r in scan["rows"] if r.get("source_type") in NON_CONTENT_TYPES):
        st.warning(f"{issue.get('target_url', 'Search')}: {issue.get('raw_text', 'Search incomplete')}")
    articles = [r for r in scan["rows"] if r.get("source_type") == "news_article"]
    snapshot = scan["snapshot"]
    if not articles or not snapshot:
        st.info("Google News returned no articles for these searches. Try a longer time window or broader terms.")
        return
    if not scan["saved"]:
        st.warning("This scan's risk point couldn't be saved to the timeline. Search again to retry.")
    _render_risk_alerts(snapshot, "this scan")
    articles_metric, boycott_metric, tension_metric, risk_metric = st.columns(4)
    articles_metric.metric("Articles found", snapshot["article_count"])
    boycott_metric.metric("Boycott flags", snapshot["boycott_count"])
    tension_metric.metric("Border tension flags", snapshot["tension_count"] + snapshot["escalation_count"],
                          help=f"{snapshot['escalation_count']} escalation · {snapshot['tension_count']} tension")
    _risk_metric(risk_metric, "Border tension risk", snapshot)
    st.caption("Flags come from a keyword scan of headlines and need a Thailand or Cambodia mention. They are "
               "leads for review, not confirmed events. After cloud saving succeeds, select these articles for AI sentiment and boycott analysis.")
    daily = daily_flag_counts(articles)
    if daily:
        st.markdown("**Flagged coverage by publication day**")
        st.vega_lite_chart(spec=daily_chart_spec(daily), use_container_width=True)

    show_col, category_col = st.columns(2)
    show = show_col.selectbox("Show", ["All articles", "Flagged articles", "Boycott", "Border tension & escalation"],
                              key="news_show_filter")
    category = category_col.selectbox("Category", ["all"] + list(scan["searches"]), key="news_category_filter",
                                      format_func=lambda c: "All categories" if c == "all" else NEWS_CATEGORIES[c]["label"])

    def visible(article: Dict[str, Any]) -> bool:
        flags = set((article.get("news_assessment") or {}).get("flags", []))
        return ((category == "all" or category in article.get("news_categories", []))
                and (show == "All articles"
                     or (show == "Flagged articles" and flags - {"De-escalation"})
                     or (show == "Boycott" and "Boycott" in flags)
                     or (show == "Border tension & escalation" and flags & {"Border tension", "Escalation"})))

    shown = [a for a in articles if visible(a)]
    if shown:
        st.dataframe(_news_table(shown), use_container_width=True, hide_index=True, column_config={
            "Published": st.column_config.DatetimeColumn("Published (UTC)", format="D MMM YYYY, HH:mm"),
            "Link": st.column_config.LinkColumn("Link", display_text="Open article"),
        })
    else:
        st.info("No articles match this filter.")
    with st.expander("Open these searches in Google News"):
        for key, query in scan["searches"].items():
            for edition in scan["editions"]:
                st.markdown(f"[{NEWS_CATEGORIES[key]['label']} · {EDITIONS[edition]['label']}]"
                            f"({browse_url(query, scan['window'], edition)})")


def _render_news_risk_history() -> None:
    with st.container(border=True, key="news_risk_history"):
        if st.button("Refresh risk timeline", key="refresh_news_history"):
            _load_news_history.clear()
        try:
            rows = _load_news_history()
        except Exception:
            st.warning("Border tension history could not be loaded. Refresh the timeline to try again.")
            return
        if not rows:
            st.info("Search Google News in Scrape Data → Online news to start tracking border tension risk.")
            return
        latest = rows[-1]
        _render_risk_alerts(latest, "the latest scan")
        risk_metric, boycott_metric, scans_metric = st.columns(3)
        _risk_metric(risk_metric, "Latest border tension risk", latest)
        boycott_metric.metric("Boycott articles (latest scan)", latest["boycott_count"])
        scans_metric.metric("News scans", len(rows))
        st.vega_lite_chart(spec=risk_chart_spec(rows), use_container_width=True)
        st.caption("Risk = share of Thailand/Cambodia articles reporting border tension, where escalation "
                   "(clashes, shelling, casualties, evacuations) counts double and ceasefire or talks coverage "
                   "lowers an article by one level. Scans with fewer than 10 such articles are scaled as if they "
                   "had 10. Low < 20 ≤ Elevated < 45 ≤ High. Compare scans that use the same searches and time window.")
        with st.expander("Scan history"):
            st.dataframe(pd.DataFrame([{
                "Scanned (UTC)": row["scanned_at"],
                "Risk": row["risk_score"], "Level": row["risk_level"],
                "Change": row.get("risk_delta"),
                "Escalation": row["escalation_count"], "Tension": row["tension_count"],
                "De-escalation": row["deescalation_count"], "Boycott": row["boycott_count"],
                "Articles": row["article_count"],
                "Window": TIME_WINDOWS.get(row.get("window", ""), row.get("window", "")),
                "Searches": " | ".join(row.get("searches", {}).values()),
            } for row in reversed(rows)]), use_container_width=True, hide_index=True)


def _save_x_collection_draft() -> None:
    """Keep inputs independently of the dialog's short-lived widgets."""
    step = st.session_state.get("x_collection_step", 2)
    if step == 2:
        options = st.session_state.get("x_collection_options", {})
        enabled = {key: st.session_state.get(f"x_enabled_{key}", options.get("enabled", {}).get(key, True))
                   for key in X_SEARCHES}
        queries = {key: st.session_state.get(f"x_query_{key}", options.get("queries", {}).get(key, category["query"]))
                   for key, category in X_SEARCHES.items()}
        searches = {key: query.strip() for key, query in queries.items() if enabled[key] and query.strip()}
        st.session_state["x_collection_options"] = {
            "searches": searches, "enabled": enabled, "queries": queries,
            "limit": int(st.session_state.get("x_post_limit", options.get("limit", 10))),
            "replies": int(st.session_state.get("x_reply_limit", options.get("replies", 0))),
        }


def _set_x_collection_step(step: int) -> None:
    _save_x_collection_draft()
    if step == 3 and not st.session_state.get("x_collection_options", {}).get("searches"):
        return
    st.session_state["x_collection_step"] = step


def _open_x_collection_dialog() -> None:
    _close_focus_modal()
    st.session_state["x_focus_open"] = True


def _close_x_collection_dialog() -> None:
    _save_x_collection_draft()
    st.session_state["x_focus_open"] = False


def _render_x_collection_setup() -> dict[str, Any] | None:
    """Keep one setup step visible, preserving drafts when widgets are hidden."""
    step = st.session_state.get("x_collection_step", 2)
    options = st.session_state.get("x_collection_options", {})
    if step not in {2, 3}:
        step = 2
    if step == 3 and not options.get("searches"):
        step = 2
    st.session_state["x_collection_step"] = step
    st.caption(f"Step {step - 1} of 2 · Choose options → Collect posts")

    connected = x_configured()
    if step == 2:
        with st.container(border=True, key="x_settings"):
            st.markdown("#### 1. Choose collection options")
            st.caption("Choose the searches to include and edit their terms. "
                       "X covers about the last 7 days. Reposts are skipped.")
            if connected:
                st.caption("X is connected. Access and available credit are checked when collecting.")
            else:
                st.info("X is not connected yet. Ask an administrator to connect X before collecting posts.")
            searches: dict[str, str] = {}
            enabled_searches, queries = {}, {}
            for key, category in X_SEARCHES.items():
                toggle, terms = st.columns([2, 3], vertical_alignment="center")
                enabled_searches[key] = toggle.checkbox(category["label"],
                    value=options.get("enabled", {}).get(key, True), key=f"x_enabled_{key}")
                queries[key] = terms.text_input(f"{category['label']} search terms",
                    value=options.get("queries", {}).get(key, category["query"]), key=f"x_query_{key}",
                    label_visibility="collapsed", disabled=not enabled_searches[key])
                if enabled_searches[key] and queries[key].strip():
                    searches[key] = queries[key].strip()
            limit_col, reply_col = st.columns(2)
            limit = limit_col.number_input("Posts per search", min_value=10, max_value=100,
                value=options.get("limit", 10), step=10, key="x_post_limit")
            replies = reply_col.number_input("Replies per post", min_value=0, max_value=10,
                value=options.get("replies", 0), step=1, key="x_reply_limit")
            _save_x_collection_draft()
            reads = estimate_reads(len(searches), int(limit), int(replies))
            st.caption(f"Up to {reads:,} posts read · Estimated cost: ${reads * READ_PRICE_USD:,.2f}")
            st.caption("Replies are a separate capped sample and are off by default. "
                       "A reply page asks for at least 10 posts even when fewer are kept.")
            if not searches:
                st.info("Enable at least one search and enter its search terms to continue.")
            st.button("Continue to collect", key="x_options_next", type="primary", disabled=not searches,
                             on_click=_set_x_collection_step, args=(3,))
        return None

    st.markdown("#### 2. Collect posts")
    reads = estimate_reads(len(options["searches"]), options["limit"], options["replies"])
    st.caption(f"{len(options['searches'])} searches · {options['limit']} posts per search · "
               f"{options['replies']} replies per post")
    st.caption(f"Up to {reads:,} posts read · Estimated cost: ${reads * READ_PRICE_USD:,.2f}. "
               "Collected posts save automatically with a source and timestamp name. Rename it in Clean & manage data.")
    st.button("Back to options", key="x_collect_back", on_click=_set_x_collection_step, args=(2,))
    if not connected:
        st.info("X is not connected yet. Ask an administrator to connect X before collecting posts.")
    can_collect = connected and not _collection_save_pending("x_collection")
    with st.container(border=True, key="x_collect_action"):
        if st.button("Collect posts", key="collect_x_posts", type="primary", use_container_width=True,
                     disabled=not can_collect) and can_collect:
            return dict(options)
    return None


@st.dialog("Collect from X", width="medium", on_dismiss=_close_x_collection_dialog)
def _render_x_collection_dialog(user_email: str = "") -> None:
    # Dialog widgets rerun independently of main(), including its sign-in gate.
    if user_email:
        from modules.auth import allowed_emails, verify_access
        try:
            if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != user_email:
                raise PermissionError()
        except Exception:
            _close_x_collection_dialog()
            st.rerun()
    with st.container(key="x_focus_dialog"):
        _render_x_collection_dialog_body()


def _render_x_collection_dialog_body() -> None:
    settings = _render_x_collection_setup()
    if settings:
        progress = st.progress(0, text="Starting X collection…")
        with running_glow(), st.spinner("Collecting recent posts…"):
            try:
                client = XClient(time.monotonic() + 180)
                rows = collect_x(settings["searches"], max_posts=settings["limit"],
                                 replies_per_post=settings["replies"], client=client,
                                 on_progress=lambda done, total, label: progress.progress(
                                     done / total, text=f"Searched {done} of {total}: {label}"))
                st.session_state["x_collection"] = {"rows": rows, "reads": client.reads, "error": ""}
                _remember_collection(rows, "X")
                _autosave_collection(st.session_state["x_collection"], "X")
            except XError as exc:
                st.session_state["x_collection"] = {"rows": [], "reads": 0, "error": str(exc)}
                _remember_collection([], "X")
            except Exception as exc:
                logging.getLogger(__name__).exception("X collection failed")
                st.session_state["x_collection"] = {
                    "rows": [], "reads": 0, "error": str(exc).strip() or "X collection failed.",
                }
                _remember_collection([], "X")
        st.session_state["x_focus_open"] = _collection_save_pending("x_collection")
        st.rerun()

    if _collection_save_pending("x_collection"):
        _render_autosave_status(st.session_state["x_collection"], "retry_x_save_dialog",
                                on_saved=_close_x_collection_dialog)


def _render_x_workspace(user_email: str = "") -> None:
    st.subheader("X · recent posts")
    st.caption("Choose your searches, then collect recent X posts in a focused window.")
    st.html(Path(__file__).parent / "modules" / "theme" / "x-focus.css")
    label = "Resume X collection" if st.session_state.get("x_collection_options") else "Start X collection"
    st.button(label, key="open_x_collection", type="primary", use_container_width=True,
              on_click=_open_x_collection_dialog)
    if st.session_state.get("x_focus_open"):
        _render_x_collection_dialog(user_email)

    collection = st.session_state.get("x_collection")
    if not collection:
        return
    _render_autosave_status(collection, "retry_x_save")
    if collection.get("error"):
        st.error(collection["error"])
    reads = int(collection.get("reads") or 0)
    if reads:
        st.caption(f"X returned {reads:,} posts for this run, about ${reads * READ_PRICE_USD:,.2f}.")
    rows = collection.get("rows") or []
    for issue in (row for row in rows if row.get("source_type") in NON_CONTENT_TYPES):
        st.warning(f"{issue.get('target_url', 'Search')}: {issue.get('raw_text', 'Collection incomplete')}")
    posts = [row for row in rows if row.get("source_type") not in NON_CONTENT_TYPES]
    st.write(f"**Records collected:** {len(posts)}")
    if not posts:
        st.info("No X posts were collected. Check any connection notices above, or broaden the search.")
        return
    st.dataframe(pd.DataFrame(posts), use_container_width=True, hide_index=True)
    with st.expander("Raw JSON Output"):
        st.json(posts)


WEBSITE_STEP_KEY = "website_wizard_step"


def _render_website_workspace(user_email: str = "") -> None:
    """Open the guided steps in a focused modal: choose your links, then collect the pages."""
    labels = ["Your links", "Collect pages"]
    current = st.session_state.get(WEBSITE_STEP_KEY, labels[0])
    if current not in labels:
        current = labels[0]
    st.session_state[WEBSITE_STEP_KEY] = current
    index = labels.index(current)
    if _focus_modal_open("website"):
        _website_steps_modal(user_email, labels, index)
        return
    with st.container(border=True, key="website_overview"):
        st.subheader("Custom website")
        st.caption("Collect public article, blog, company or other website pages for sentiment analysis. "
                   f"Up to {MAX_WEBSITE_LINKS} unique pages per run.")
        urls = st.session_state.get("website_wizard_urls", [])
        st.caption(f"{len(urls)} link{'s' if len(urls) != 1 else ''} ready to collect.")
        collection = st.session_state.get("website_collection")
        if collection:
            pages = [row for row in collection["rows"] if row.get("source_type") not in NON_CONTENT_TYPES]
            st.caption(f"Last collection: {len(pages)} page{'s' if len(pages) != 1 else ''}. "
                       "Open the steps to review or save them.")
        _render_focus_trigger("website", labels, index, "Start collecting website pages")


@st.dialog("Collect website pages", width="large", on_dismiss=_close_focus_modal)
def _website_steps_modal(user_email: str, labels: List[str], index: int) -> None:
    # Dialog widgets rerun independently of main(), including its sign-in gate.
    if user_email:
        from modules.auth import allowed_emails, verify_access
        try:
            if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != user_email:
                raise PermissionError()
        except Exception:
            _close_focus_modal()
            st.rerun()
    # Dialog reruns retain their original call arguments. Read the live step so
    # Next and Previous navigation take effect immediately.
    current = st.session_state.get(WEBSITE_STEP_KEY, labels[index] if index < len(labels) else labels[0])
    if current not in labels:
        current = labels[0]
    index = labels.index(current)
    _render_step_header(labels, index)
    if labels[index] == "Your links":
        _render_website_links_step(user_email)
        can_continue = bool(st.session_state.get("website_wizard_urls"))
    else:
        _render_website_collect_step()
        can_continue = False
    _render_step_nav(WEBSITE_STEP_KEY, labels, index, can_continue)


def _render_website_links_step(user_email: str) -> None:
    if "website_links" not in st.session_state:
        # Streamlit drops widget state while this step is hidden; restore the earlier links.
        st.session_state["website_links"] = st.session_state.get("website_links_memo", "")
    links = st.text_area("Website links (one per line)", key="website_links", height=160,
                         placeholder="https://example.com/article\nhttps://example.org/news/update",
                         help=f"Up to {MAX_WEBSITE_LINKS} unique pages per run. Only the links you enter are collected; linked pages are not crawled.")
    st.session_state["website_links_memo"] = links
    if user_email:
        render_website_presets(user_email)
    try:
        urls = parse_website_links(links)
    except WebsiteError as exc:
        st.session_state["website_wizard_urls"] = []
        if links.strip():
            st.error(str(exc))
        return
    st.session_state["website_wizard_urls"] = urls
    if not urls:
        st.caption("Add at least one website link to continue.")
    else:
        st.caption(f"{len(urls)} page{'s' if len(urls) != 1 else ''} ready to collect")


def _render_website_collect_step() -> None:
    urls = list(st.session_state.get("website_wizard_urls", []))
    st.caption(f"Collecting {len(urls)} page{'s' if len(urls) != 1 else ''}. Go back to change the links.")
    with st.expander(f"Links for this collection ({len(urls)})"):
        st.code("\n".join(urls))
    if st.button("Collect website pages", key="collect_website_pages", type="primary",
                 use_container_width=True,
                 disabled=not urls or _collection_save_pending("website_collection")) and not _collection_save_pending("website_collection"):
        st.session_state["website_collection"] = {"urls": urls, "rows": []}
        progress = st.progress(0, text="Starting website collection…")
        with running_glow(), st.spinner("Reading website pages…"):
            rows = collect_websites(urls, on_progress=lambda done, total, url:
                                    progress.progress(done / total, text=f"Checked {done} of {total} pages"))
            st.session_state["website_collection"]["rows"] = rows
            _remember_collection(rows, "Custom website")
            _autosave_collection(st.session_state["website_collection"], "Custom website")
        st.rerun()
    collection = st.session_state.get("website_collection")
    if collection is None:
        return
    _render_autosave_status(collection, "retry_website_save")
    with st.expander("Sources in the last website collection"):
        st.code("\n".join(collection["urls"]))
    rows = collection["rows"]
    for issue in (row for row in rows if row.get("source_type") in NON_CONTENT_TYPES):
        st.warning(f"{issue.get('target_url', 'Website')}: {issue.get('raw_text', 'Collection incomplete')}")
    pages = [row for row in rows if row.get("source_type") not in NON_CONTENT_TYPES]
    st.write(f"**Pages collected:** {len(pages)}")
    if not pages:
        st.info("No readable website pages were collected. Check the links and collection notices above.")
        return
    if any(page.get("text_truncated") for page in pages):
        st.caption("Long pages were limited to the first 60,000 characters. Review the extracted text before analyzing.")
    for page in pages:
        with st.expander(page.get("title") or page["url"]):
            st.link_button("Open source page", page["url"])
            st.text(page["raw_text"])

    if not collection.get("cloud_save") and st.button("Save collected pages", key="save_website_dataset"):
        _autosave_collection(collection, "Custom website")
        st.rerun()


ANALYSIS_STEP_KEY = "analysis_wizard_step"
ANALYSIS_MEMO_KEY = "analysis_selection_memo"
ANALYSIS_WATCH_MEMO_KEY = "analysis_watch_terms_memo"
ANALYSIS_STEPS = ["Choose and edit data", "Analyze"]


def _analysis_selection() -> Dict[str, Any]:
    """The chosen data, read from the step widgets while shown and from the memo while hidden."""
    memo = st.session_state.get(ANALYSIS_MEMO_KEY, {})
    mode = st.session_state.get("analysis_selection_mode") or memo.get("mode") or "Manual selection"
    use_recent = mode == "Last scraped data"
    recent = st.session_state.get("last_collected_dataset") if use_recent else None
    recent_ids = {row["_id"] for row in recent["rows"]} if recent else set()
    if use_recent:
        return {"mode": mode, "use_recent": True, "recent": recent, "recent_ids": recent_ids,
                "datasets": ["recent:" + recent["id"]] if recent else [], "sources": None,
                "ready": bool(recent_ids)}
    sources = _selected_analysis_sources(memo.get("sources"))
    datasets = list(st.session_state.get("analysis_datasets", memo.get("datasets") or []))
    return {"mode": mode, "use_recent": False, "recent": None, "recent_ids": set(),
            "datasets": datasets, "sources": sources, "ready": bool(sources) and bool(datasets)}


def _remember_analysis_selection(picked: Dict[str, Any]) -> None:
    st.session_state[ANALYSIS_MEMO_KEY] = {"mode": picked["mode"], "sources": picked["sources"],
                                           "datasets": picked["datasets"]}


def _in_analysis_selection(row: Dict[str, Any], picked: Dict[str, Any]) -> bool:
    if not picked["ready"]:
        return False
    if picked["use_recent"]:
        return row.get("_id") in picked["recent_ids"]
    return (dataset_key(row) in picked["datasets"]
            and (picked["sources"] is None or matches_sources(row, set(picked["sources"]))))


def _loaded_analysis_rows(picked: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [row for row in st.session_state.get("firestore_rows", []) if _in_analysis_selection(row, picked)]


def _render_analysis_tab(user_email: str) -> list[str] | None:
    """Settings stay on their own tab; the analysis itself runs as guided steps in a focused modal."""
    st.subheader("Sentiment Analysis")
    analysis_workspace, analysis_settings = st.tabs(["Analysis", "Settings"])
    picked = _analysis_selection()

    with analysis_settings:
        st.subheader("Analysis settings")
        status_filter = st.selectbox("Record status", options=["all", "pending", "processed", "error"], index=0,
                                     key="analysis_recent_status" if picked["use_recent"] else "analysis_record_status")
        load_limit = None
        _sync_analysis_datasets(picked["datasets"], status_filter)
        provider = _render_ai_settings()
        provider_label = AI_PROVIDERS[provider]

    with analysis_workspace:
        current = st.session_state.get(ANALYSIS_STEP_KEY, ANALYSIS_STEPS[0])
        if current not in ANALYSIS_STEPS:
            current = ANALYSIS_STEPS[0]
        st.session_state[ANALYSIS_STEP_KEY] = current
        index = ANALYSIS_STEPS.index(current)
        if _focus_modal_open("analysis"):
            _analysis_steps_modal(user_email, picked, index, status_filter, load_limit, provider, provider_label)
        else:
            _render_analysis_overview(picked, index, provider_label)
        _render_analysis_results()
        picked = _analysis_selection()
        _render_analysis_processed_section(picked, status_filter, load_limit)
    return picked["sources"]


def _render_analysis_overview(picked: Dict[str, Any], index: int, provider_label: str) -> None:
    with st.container(border=True, key="analysis_overview"):
        st.markdown("#### Guided analysis")
        if picked["use_recent"]:
            recent = picked["recent"]
            st.caption(f"Data: last scraped · {recent['title']}" if recent else
                       "Data: last scraped collection — nothing collected in this browser session yet.")
        elif picked["ready"]:
            st.caption(f"Data: {len(picked['datasets'])} saved dataset"
                       f"{'s' if len(picked['datasets']) != 1 else ''} from "
                       f"{len(picked['sources'])} source{'s' if len(picked['sources']) != 1 else ''}.")
        else:
            st.caption("Data: not chosen yet. The first step picks the records to analyze.")
        loaded = _loaded_analysis_rows(picked)
        st.caption(f"{len(loaded)} record{'s' if len(loaded) != 1 else ''} available · AI provider: {provider_label}")
        _render_focus_trigger("analysis", ANALYSIS_STEPS, index, "Start sentiment analysis")


@st.dialog("Run sentiment analysis", width="large", on_dismiss=_close_focus_modal)
def _analysis_steps_modal(user_email: str, picked: Dict[str, Any], index: int, status_filter: str,
                          load_limit: int | None, provider: str, provider_label: str) -> None:
    # Dialog widgets rerun independently of main(), including its sign-in gate.
    if user_email:
        from modules.auth import allowed_emails, verify_access
        try:
            if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != user_email:
                raise PermissionError()
        except Exception:
            _close_focus_modal()
            st.rerun()
    # Dialog reruns retain their original call arguments. Read the live step and
    # selection so Next/Previous and dataset edits take effect immediately.
    picked = _analysis_selection()
    current = st.session_state.get(ANALYSIS_STEP_KEY, ANALYSIS_STEPS[0])
    index = ANALYSIS_STEPS.index(current) if current in ANALYSIS_STEPS else 0
    _render_step_header(ANALYSIS_STEPS, index)
    current = ANALYSIS_STEPS[index]
    if current == "Choose and edit data":
        picked = _render_analysis_data_step(user_email, picked)
        _sync_analysis_datasets(picked["datasets"], status_filter)
        rows = _open_analysis_dataset(picked)
        visible = [row for row in rows if status_filter == "all" or row.get("status") == status_filter]
        if rows:
            included, saved = render_dataset_editor(visible)
            st.session_state["analysis_edits_saved"] = saved
            can_continue = bool(included) and saved
        else:
            can_continue = False
    else:
        _render_analysis_run_step(picked, provider, provider_label, status_filter)
        can_continue = False
    _render_step_nav(ANALYSIS_STEP_KEY, ANALYSIS_STEPS, index, can_continue)


def _render_analysis_data_step(user_email: str, picked: Dict[str, Any]) -> Dict[str, Any]:
    # Streamlit drops widget state while a step is hidden; restore the earlier choice first.
    st.session_state.setdefault("analysis_selection_mode", picked["mode"])
    for key, value in (("analysis_sources", picked["sources"]), ("analysis_datasets", picked["datasets"])):
        if key not in st.session_state and value:
            st.session_state[key] = list(value)
    selection_mode = st.radio("Data selection", ["Manual selection", "Last scraped data"],
                              horizontal=True, key="analysis_selection_mode")
    if st.session_state.get("analysis_previous_mode") != selection_mode:
        _clear_loaded_analysis()
        st.session_state.pop("analysis_dataset_context", None)
        st.session_state["analysis_previous_mode"] = selection_mode
    if selection_mode == "Last scraped data":
        recent = st.session_state.get("last_collected_dataset")
        st.caption("The most recently completed collection in this browser session, across all scraping options. "
                   "Only records from that collection are included. After reopening the app, use Manual selection to find saved data.")
        if recent:
            st.write(f"**{recent['title']}**")
            collected_at = dataset_date_label({"timestamp": datetime.fromtimestamp(recent["completed_at"], timezone.utc)})
            analyzable = len({row["_id"] for row in recent["rows"]})
            st.caption(f"Source: {recent['source']} · Collected {collected_at} · {analyzable} analyzable records")
            if not analyzable:
                st.info("No analyzable records remain in the last collection. Collect again or choose a saved dataset manually.")
            elif not recent.get("saved"):
                st.caption("Opening this collection saves it to your library. Review its records below before running analysis.")
        else:
            st.info("Collect data in Scrape Data first, then choose Last scraped data here. "
                    "To analyze an earlier saved collection, choose Manual selection.")
    else:
        sources, sources_ready = _render_analysis_sources(user_email)
        _render_analysis_datasets(sources, sources_ready)
    picked = _analysis_selection()
    _remember_analysis_selection(picked)
    if not picked["ready"] and not picked["use_recent"]:
        st.caption("Choose at least one source and one dataset to continue.")
    return picked


def _open_analysis_dataset(picked: Dict[str, Any]) -> List[Dict[str, Any]]:
    if picked["ready"] and "firestore_rows" not in st.session_state:
        try:
            with st.spinner("Opening dataset…"):
                if picked["use_recent"]:
                    recent = picked["recent"]
                    if not recent.get("saved"):
                        _write_results_to_firestore(recent["rows"], custom_title=recent["title"])
                        recent["saved"] = True
                    rows = _fetch_last_scrape_posts(recent, status_filter="all", limit=None)
                else:
                    rows = _fetch_firestore_posts(status_filter="all", limit=None,
                                                 source_urls=picked["sources"], dataset=picked["datasets"])
            st.session_state["firestore_rows"] = rows
            st.session_state["firestore_load_error"] = ""
        except Exception:
            st.session_state["firestore_rows"] = []
            st.session_state["firestore_load_error"] = "The dataset could not be opened. Check the connection and try again."
    rows = _loaded_analysis_rows(picked)
    if st.session_state.get("firestore_load_error"):
        st.error(st.session_state["firestore_load_error"])
        if st.button("Retry opening dataset", key="retry_analysis_dataset"):
            _clear_loaded_analysis()
            st.rerun()
    elif picked["ready"] and not rows:
        st.info("This dataset has no available records. Refresh datasets or choose another dataset.")
    return rows


def _render_analysis_run_step(picked: Dict[str, Any], provider: str, provider_label: str,
                              status_filter: str = "all") -> None:
    firestore_rows = _loaded_analysis_rows(picked)
    if not firestore_rows:
        st.info("Choose a dataset in the previous step to see its records.")
        return
    if "thailand_watch_terms" not in st.session_state:
        st.session_state["thailand_watch_terms"] = st.session_state.get(
            ANALYSIS_WATCH_MEMO_KEY, get_setting("THAILAND_WATCH_TERMS"))
    terms = st.text_area("Thai projects, companies and brands to watch (one name or alias per line)",
                         key="thailand_watch_terms",
                         help="Leave blank for Thailand generally. Add the names and local-language aliases of your Thai-linked targets.")
    st.session_state[ANALYSIS_WATCH_MEMO_KEY] = terms
    selected_posts = [row for row in firestore_rows if is_analyzable(row) and not row.get("analysis_excluded")
                      and (status_filter == "all" or row.get("status") == status_filter)]
    count = len(selected_posts)
    st.caption(f"{count} included record{'s' if count != 1 else ''} ready for {provider_label}. "
               "Go back to change which records are included.")
    if st.button(f"Run {provider_label} on {count} included record{'s' if count != 1 else ''}",
                 key="run_analysis_selected_btn",
                 disabled=not get_ai_key(provider) or not count or not picked["ready"]
                 or not st.session_state.get("analysis_edits_saved", True)):
        glow = show_running_glow()
        status_container = st.status(
            f"Running {provider_label} on {len(selected_posts)} record(s)...",
            expanded=True,
        )
        try:
            def _update_status(msg: str) -> None:
                status_container.write(msg)

            summary = _process_selected_firestore_posts(
                selected_posts,
                provider=provider,
                status_callback=_update_status,
            )
            st.session_state["analysis_last_results"] = summary
            st.session_state["analysis_editor_revision"] = st.session_state.get("analysis_editor_revision", 0) + 1
            status_container.update(
                label=(
                    f"Done — {summary['processed']} processed, "
                    f"{summary['error']} error(s)"
                ),
                state="error" if summary["error"] else "complete",
            )
        except Exception as exc:
            glow.empty()
            error_text = (str(exc).strip() if isinstance(exc, (AIError, ValueError)) else
                          "Analysis failed. Please try again or contact the app administrator.")
            error_text = redact(error_text, get_ai_key(provider))
            status_container.update(
                label=f"Failed: {error_text}",
                state="error",
            )
            st.session_state["analysis_last_results"] = {
                "processed": 0, "error": len(selected_posts), "analyses": [], "errors": [error_text],
            }
        else:
            # The finished run belongs on the page, where the report has room to be read.
            _close_focus_modal()
            st.rerun()


def _render_analysis_results() -> None:
    analysis_results = st.session_state.get("analysis_last_results")
    if not analysis_results:
        return
    st.divider()
    st.subheader("Analysis results")

    res_c1, res_c2 = st.columns(2)
    res_c1.metric("Processed", analysis_results.get("processed", 0))
    res_c2.metric("Errors", analysis_results.get("error", 0))

    for message in dict.fromkeys(analysis_results.get("errors", [])):
        st.error(message)
    analyses = analysis_results.get("analyses", [])
    if analyses:
        def evidence() -> None:
            visible = _render_signal_overview(analyses, "latest_signals_filter")
            _render_analysis_cards(visible)
            with st.expander("Raw analysis JSON"):
                st.json(analyses)

        report_date = max((str(a.get("processed_at") or "") for a in analyses), default="") or _now_iso()
        _render_client_report(analyses, "latest_report", report_date, evidence)
    elif analysis_results.get("error", 0) > 0:
        st.warning("No records were analyzed. Resolve the error above and retry."
                   if analysis_results.get("errors") else
                   "This result was created before detailed errors were available. Run analysis again to see the cause.")


def _render_analysis_processed_section(picked: Dict[str, Any], status_filter: str, load_limit: int | None) -> None:
    st.divider()
    st.subheader("View processed records")
    processed_rows = [row for row in _loaded_analysis_rows(picked) if row.get("status") == "processed"
                      and not row.get("analysis_excluded")]
    st.session_state["processed_rows"] = processed_rows
    st.caption("Saved analyses for the included records in your selected datasets appear here automatically.")
    _render_processed_deletion(processed_rows)
    if processed_rows:
        _render_processed_records(processed_rows)


def _select_scrape_source(source: str | None) -> None:
    st.session_state["scrape_source"] = source
    _close_focus_modal()
    if st.session_state.get("x_focus_open"):
        _close_x_collection_dialog()
    if source in {"facebook", "facebook_search", "website"}:
        _open_focus_modal("facebook" if source == "facebook_search" else source)
    elif source == "x":
        _open_x_collection_dialog()


def _render_scrape_workspace(user_email: str, facebook_session: FacebookSession) -> None:
    st.subheader("Scrape Data")
    collect_tab, saved_links_tab = st.tabs(["Collect data", "Saved Facebook links"])
    with collect_tab:
        render_scrape_navigation(_select_scrape_source)
        with st.container(key="sm_scrape_panel_facebook"):
            _render_collection_workspace("facebook", user_email, facebook_session)
        with st.container(key="sm_scrape_panel_reddit"):
            _render_collection_workspace("reddit", user_email, facebook_session)
        with st.container(key="sm_scrape_panel_news"):
            _render_news_workspace()
        with st.container(key="sm_scrape_panel_x"):
            _render_x_workspace(user_email)
        with st.container(key="sm_scrape_panel_website"):
            _render_website_workspace(user_email)
    with saved_links_tab:
        # Tab interactions rerun main(), including its account access check.
        render_saved_facebook_links(user_email)


def main() -> None:
    load_environment()
    _bridge_secrets_to_env()
    icon = Path(__file__).resolve().parent / "static" / "apple-touch-icon.png"
    st.set_page_config(page_title="CrisisRadar", page_icon=str(icon), layout="wide")
    _apply_ui_theme()
    user_email = require_access()
    if user_email is None:
        # Finish the script normally so Streamlit mounts the sign-in iframe.
        # st.stop() here can leave new component frames at about:blank.
        return
    render_browserbase_usage_bar(user_email)
    alerts_slot = st.container(key="sm_global_alerts")
    try:
        facebook_session = (remote_facebook_session(user_email) if IS_CLOUD else
                            _get_facebook_session(_facebook_storage_path()))
    except Exception:
        facebook_session = FacebookSession()
    scrape_tab, records_tab, analysis_tab, velocity_tab, trends_tab, content_tab, gencite_tab, tutorial_tab = dashboard_views(user_email, facebook_session)

    with scrape_tab:
        _render_scrape_workspace(user_email, facebook_session)

    with records_tab:
        render_record_management()

    with analysis_tab:
        _render_analysis_tab(user_email)

    with velocity_tab:
        render_velocity_workspace(user_email, hosted=IS_CLOUD)

    # Render after processing so the trends page includes the latest saved run.
    with trends_tab:
        _render_sentiment_trends()

    with content_tab:
        render_content_workspace()

    with gencite_tab:
        render_gencite_workspace(user_email)

    with tutorial_tab:
        render_tutorial_workspace()

    try:
        saved_sentiment_history = _load_sentiment_history()
    except Exception:
        saved_sentiment_history = []
    try:
        saved_news_history = _load_news_history()
    except Exception:
        saved_news_history = []
    with alerts_slot:
        render_global_alerts(
            user_email,
            sentiment_history=saved_sentiment_history,
            news_history=saved_news_history,
        )


if __name__ == "__main__":
    main()

