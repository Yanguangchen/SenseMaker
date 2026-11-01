"""Frontend-first Streamlit dashboard for Project Sentinel."""

from __future__ import annotations

import asyncio
import html
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st

from modules.auth import require_access
from modules.analysis_library import select_history, select_records, source_key
from modules.navigation import dashboard_views
from modules.ai_errors import AIError, redact
from modules.config import AI_PROVIDERS, get_ai_key, get_ai_model, get_ai_provider, get_setting, load_environment
from modules.database import delete_processed_posts, init_db, upsert_post
from modules.gencite_ui import render_gencite_workspace
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
from modules.collection_jobs import active_collection, cancel_collection, collection_job, start_collection
from modules.processor import analyze_posts
from modules.gemini import AUTO_MODEL, GeminiError, list_text_models, normalize_model_name
from modules.reddit import CAMBODIA_URL, is_reddit_url, reddit_configured
from modules.signals import NON_CONTENT_TYPES, SIGNAL_LABELS, is_analyzable, is_flagged, watch_terms
from modules.saved_links import library_owner_id, load_saved_links, parse_links, remove_saved_links, save_links
from modules.sentiment_history import (
    NEWS_SOURCE, chart_spec, combined_history, load_history, news_sentiment_rows, persist_analysis_run,
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
        payload["saved_at"] = _now_iso()
        if not payload.get("scraped_at"):
            payload["scraped_at"] = save_timestamp
        upsert_post(db, payload)
        inserted += 1
    return {"inserted": inserted, "skipped": skipped}


def _fetch_firestore_posts(
    status_filter: str, limit: int, source_urls: list[str] | None = None,
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
        rows.append(item)
    return select_records(rows, source_urls=source_urls, limit=limit)


def _render_analysis_sources(user_email: str) -> tuple[list[str] | None, bool]:
    scope = st.selectbox("Record sources", ["Current sources", "All saved records (including older sources)"],
                         key="analysis_source_scope")
    sources: list[str] | None = None
    ready = True
    if scope == "Current sources":
        library = st.session_state.get(f"saved_links_{library_owner_id(user_email)}_facebook", {})
        ready = bool(library) and not library.get("error", False)
        sources = sorted(set([CAMBODIA_URL] + library.get("urls", []))) if ready else []
        st.caption("Uses your saved Facebook sources, r/cambodia and saved Online News articles. "
                   "Older groups appear only when you choose all saved records. Newest collected records load first.")
        if not ready:
            st.warning("Your source library could not be loaded. Refresh saved links in Scrape Data → Facebook, "
                       "then try again.")
        elif sources:
            with st.expander("Current analysis sources"):
                st.code("\n".join(sources))
                st.caption("Plus articles you save from Online News.")
    else:
        st.warning("This includes previous research from sources outside your current saved list.")
    context = (get_setting("FIREBASE_COLLECTION", "raw_posts"), scope, ready,
               tuple(sorted(source_key(url) for url in (sources or []))))
    if st.session_state.get("analysis_library_context") != context:
        # Includes sessions created before source selection existed. No stale rows
        # or prior AI results can survive a change of source library.
        for key in ("firestore_rows", "processed_rows", "analysis_last_results", "gemini_last_results",
                    "firestore_load_error", "firestore_last_load_count", "analysis_record_selection",
                    "processed_delete_selection", "processed_delete_pending", "processed_delete_notice"):
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
    targets = watch_terms(st.session_state.get("thailand_watch_terms", ""))
    analysis_by_id = analyze_posts(
        selected_posts,
        provider=provider,
        model_name=model_name,
        on_status=status_callback,
        targets=targets,
    )
    snapshot = persist_analysis_run(db, selected_posts, analysis_by_id, provider=provider, targets=targets)
    _load_sentiment_history.clear()
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
            })
        else:
            errored += 1
    return {"processed": processed, "error": errored, "analyses": analyses,
            "errors": list(getattr(analysis_by_id, "errors", [])), "history_run": snapshot}


@st.cache_data(ttl=60, show_spinner=False)
def _load_sentiment_history() -> list[dict[str, Any]]:
    return load_history(init_db())


def _render_sentiment_history(source_urls: list[str] | None = None) -> None:
    with st.container(border=True, key="sentiment_history"):
        st.subheader("Sentiment over time")
        st.caption("One point per AI analysis and per Online News search · Higher is more positive · "
                   "Dashed line is the average of all points · Red rings mark border tension or boycott signals")
        if st.button("Refresh timeline", key="refresh_sentiment_history"):
            _load_sentiment_history.clear()
            _load_news_history.clear()
        try:
            analysis_rows = _load_sentiment_history()
        except Exception:
            st.warning("Sentiment history could not be loaded. Refresh the timeline to try again.")
            return
        visible_analysis = select_history(analysis_rows, source_urls)
        if len(visible_analysis) < len(analysis_rows):
            st.caption("Previous runs from other or unrecorded sources are hidden. "
                       "Choose all saved records in Sentiment Analysis to view the full history.")
        analysis_rows = visible_analysis
        try:
            news_rows = news_sentiment_rows(_load_news_history())
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


def _render_gemini_settings() -> None:
    api_key = get_ai_key("gemini")
    fingerprint = hashlib.sha256(api_key.strip().encode()).hexdigest()
    if st.session_state.get("gemini_catalog_key") != fingerprint:
        st.session_state["gemini_catalog_key"] = fingerprint
        st.session_state.pop("gemini_available_models", None)
        st.session_state.pop("gemini_catalog_error", None)
    if st.button("Refresh available models", key="refresh_gemini_models", use_container_width=True,
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


def _render_ai_settings() -> str:
    if "ai_provider" not in st.session_state:
        st.session_state["ai_provider"] = get_ai_provider()
    provider = st.selectbox("AI provider", list(AI_PROVIDERS), key="ai_provider",
                            format_func=AI_PROVIDERS.get)
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
            rows = await scrape_one(url)
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
    if job.status == "saving":
        st.info("Saving your Facebook login…")
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
            st.info("Checking your Facebook sign-in…")
    elif job.status == "checking":
        st.info("Checking your saved Facebook connection…")
    else:
        st.info("Opening your Facebook login window…")
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
            st.caption("Sign in through the interactive browser. Your saved login is private to your SenseMaker account.")
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
                             "The error details have been recorded in the SenseMaker server log.")
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
            host = (urlsplit(url).hostname or "").lower()
            return host in {"facebook.com", "fb.com"} or host.endswith(".facebook.com")
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

    def remove_selected() -> None:
        removable = [url for url in st.session_state.get(selection_key, []) if url != CAMBODIA_URL]
        try:
            remove_saved_links(init_db(), user_email, removable)
            removed = set(removable)
            st.session_state[library_key]["urls"] = [url for url in saved if url not in removed]
            st.session_state[selection_key] = [
                url for url in st.session_state.get(selection_key, []) if url not in removed]
            st.session_state[message_key] = f"Removed {len(removable)} saved links."
        except Exception:
            st.session_state[save_error_key] = "Some links couldn’t be removed. Refresh your library and try again."

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

        removable = [url for url in selected if url != CAMBODIA_URL]
        if removable:
            st.button(
                f"Remove {len(removable)} saved link{'s' if len(removable) != 1 else ''}",
                key=widget_key("remove_selected_links"), use_container_width=True,
                help="Removes these links from your library. Collected posts are kept.",
                on_click=remove_selected,
            )

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
                           "sensemaker-findings.csv", "text/csv", key=f"{key}_export")
    else:
        st.info("No records match this filter.")
    return visible


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
                    # Drop stale copies, including records another session already removed or changed.
                    stale = set(result["deleted"] + result["missing"] + result["skipped"])
                    for key in ("processed_rows", "firestore_rows"):
                        if key in st.session_state:
                            st.session_state[key] = [row for row in st.session_state[key]
                                                     if str(row.get("_id", "")) not in stale]
                    if "firestore_rows" in st.session_state:
                        st.session_state["firestore_last_load_count"] = len(st.session_state["firestore_rows"])
                    for key in ("analysis_last_results", "gemini_last_results"):
                        if key in st.session_state:
                            summary = dict(st.session_state[key])
                            summary["analyses"] = [row for row in summary.get("analyses", [])
                                                   if str(row.get("_id", "")) not in stale]
                            summary["processed"] = len(summary["analyses"])
                            st.session_state[key] = summary
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

@media (prefers-reduced-motion: reduce) {
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


def _render_cloud_save(records: List[Dict[str, Any]], title_key: str, save_key: str, noun: str = "posts") -> None:
    custom_title = st.text_input(
        "Custom title",
        value=st.session_state.get(title_key, ""),
        key=title_key,
        help="This title will be saved on each object uploaded to Firestore.",
    )
    if st.button("Save data to cloud", key=save_key):
        try:
            if not custom_title.strip():
                st.error("Please enter a custom title before saving.")
            else:
                write_summary = _write_results_to_firestore(records, custom_title=custom_title)
                st.success(
                    "Saved to Firestore: "
                    f"{write_summary['inserted']} {noun} "
                    f"(skipped: {write_summary['skipped']})."
                )
        except Exception as exc:
            error_text = str(exc).strip() or repr(exc)
            st.error(f"Firestore write failed: {error_text}")


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
            st.session_state["last_scrape_urls"] = list(job.urls)
            st.session_state["last_scrape_error"] = job.error or (
                "Collection cancelled. Completed results have been kept." if job.status == "cancelled" else "")
        else:
            st.session_state["last_scrape_error"] = "The collection ended when the app restarted. Please try again."
        st.rerun(scope="app")
    total = len(job.urls)
    st.progress(job.completed / max(total, 1), text=f"Collection running · {job.completed} of {total} sources finished")
    if job.status == "cancelling":
        st.info("Stopping collection and releasing your Facebook connection…")
    else:
        st.caption("You can switch workspaces. Another click or browser tab will keep using this collection.")
    if st.button("Cancel collection", key="cancel_facebook_collection", disabled=job.status == "cancelling"):
        cancel_collection(email, job_id)


def _render_collection_workspace(
    platform: str, user_email: str, facebook_session: FacebookSession,
) -> None:
    if platform == "facebook":
        _render_facebook_connection(facebook_session, user_email=user_email)
        reddit_limit, reddit_comments = 50, 20
        target_urls, links_valid = _render_link_workspace(user_email, platform=platform)
    else:
        reddit_limit, reddit_comments = _render_reddit_settings()
        target_urls, links_valid = [CAMBODIA_URL], True

    if platform == "facebook":
        if target_urls and facebook_session.status not in {"connected", "saved"}:
            st.info("Connect your Facebook account above before collecting Facebook posts.")
        tc1, tc2 = st.columns(2)
        use_headless = tc1.checkbox("Headless Browser", value=True, disabled=IS_CLOUD, key="facebook_headless")
        scroll_times = tc2.slider("Scroll Iterations", min_value=1, max_value=50, value=10, key="facebook_scroll_times")
    else:
        use_headless, scroll_times = True, 10

    # Keep each source's results and save controls independent when switching tabs.
    result_key = "last_scrape" if platform == "facebook" else "last_reddit_scrape"
    title_key = "custom_title_value" if platform == "facebook" else "reddit_custom_title_value"
    save_key = "save_last_results_btn" if platform == "facebook" else "save_reddit_results_btn"
    hosted_facebook = platform == "facebook" and IS_CLOUD
    active_job = active_collection(user_email) if hosted_facebook else None
    if active_job:
        st.session_state["facebook_collection_job"] = active_job.id
    collection_id = st.session_state.get("facebook_collection_job") if hosted_facebook else None
    if collection_id:
        _render_facebook_collection_progress(user_email, collection_id)
    if st.button(
        "Collect posts", key=f"collect_{platform}_posts", type="primary", use_container_width=True,
        disabled=platform == "facebook" and IS_CLOUD and (
            facebook_session.status != "connected" or bool(st.session_state.get("facebook_login_job")) or bool(active_job)),
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
                async def run(progress, partial):
                    return await _run_multi_scrape_async(
                        urls=list(target_urls), headless=True, scroll_times=scroll_times,
                        facebook_owner=user_email, on_progress=progress, on_results=partial,
                    )
                st.session_state["facebook_collection_job"] = start_collection(user_email, target_urls, run)
                st.rerun()
            progress = st.progress(0, text=f"Starting collection for {len(target_urls)} links…")
            with st.spinner("Running scraper..."):
                try:
                    results = _run_multi_scrape_sync(
                        urls=target_urls,
                        headless=use_headless,
                        scroll_times=scroll_times,
                        reddit_limit=int(reddit_limit), reddit_comments=int(reddit_comments),
                        facebook_owner=user_email if platform == "facebook" and IS_CLOUD else None,
                        on_progress=lambda done, total, _url: progress.progress(
                            done / total, text=f"Processed {done} of {total} links"
                        ),
                    )
                    st.session_state[f"{result_key}_results"] = results
                    if any(row.get("facebook_connection_error") for row in results):
                        # Refresh connection controls from the saved check result,
                        # while retaining any posts collected before the failure.
                        st.rerun()
                except Exception as exc:
                    st.session_state[f"{result_key}_results"] = []
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
            _render_cloud_save(scrape_results, title_key, save_key)
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

    if st.button("Search Google News", key="collect_news_articles", type="primary", use_container_width=True):
        if not searches:
            st.error("Choose at least one category with search terms.")
        elif not editions:
            st.error("Choose at least one Google News edition.")
        else:
            progress = st.progress(0, text="Searching Google News…")
            with st.spinner("Scanning news coverage…"):
                rows = collect_news(searches, window=window, editions=editions, limit=int(limit),
                                    on_progress=lambda done, total, query: progress.progress(
                                        done / total, text=f"Searched {done} of {total}: {query}"))
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
            st.session_state["news_scan"] = {"rows": rows, "snapshot": snapshot, "saved": saved,
                                             "searches": searches, "window": window, "editions": editions}

    scan = st.session_state.get("news_scan")
    if not scan:
        return
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
               "leads for review, not confirmed events. Save the articles to run AI sentiment and boycott analysis.")
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
    _render_cloud_save(articles, "news_custom_title_value", "save_news_results_btn", noun="articles")


def _render_news_risk_history() -> None:
    with st.container(border=True, key="news_risk_history"):
        st.subheader("Border tension risk over time")
        st.caption("One point per Google News scan · Shaded bands mark Low, Elevated and High risk · "
                   "▲ marks a rise of 10 or more points · Dashed purple line is the boycott index")
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


def main() -> None:
    load_environment()
    _bridge_secrets_to_env()
    st.set_page_config(page_title="SenseMaker", layout="wide")
    _apply_ui_theme()
    user_email = require_access()
    if user_email is None:
        # Finish the script normally so Streamlit mounts the sign-in iframe.
        # st.stop() here can leave new component frames at about:blank.
        return
    try:
        facebook_session = (remote_facebook_session(user_email) if IS_CLOUD else
                            _get_facebook_session(_facebook_storage_path()))
    except Exception:
        facebook_session = FacebookSession()
    scrape_tab, analysis_tab, trends_tab, gencite_tab = dashboard_views(user_email, facebook_session)

    with scrape_tab:
        facebook_tab, reddit_tab, news_tab = st.tabs(["Facebook", "Reddit", "Online news"])
        with facebook_tab:
            _render_collection_workspace("facebook", user_email, facebook_session)
        with reddit_tab:
            _render_collection_workspace("reddit", user_email, facebook_session)
        with news_tab:
            _render_news_workspace()

    with analysis_tab:
        st.subheader("Sentiment Analysis")
        st.caption("Detect Thailand-related criticism, tensions and boycott calls, with source evidence.")
        analysis_sources, sources_ready = _render_analysis_sources(user_email)
        st.text_area("Thai projects, companies and brands to watch (one name or alias per line)",
                     value=get_setting("THAILAND_WATCH_TERMS"), key="thailand_watch_terms",
                     help="Leave blank for Thailand generally. Add the names and local-language aliases of your Thai-linked targets.")

        # --- Settings row ---
        gc1, gc2 = st.columns([1, 1])
        status_filter = gc1.selectbox("Record status", options=["all", "pending", "processed", "error"], index=0)
        load_limit = gc2.slider("Load limit", min_value=1, max_value=200, value=50)
        provider = _render_ai_settings()
        provider_label = AI_PROVIDERS[provider]

        # --- Load records ---
        if st.button("Load records from Firestore", key="load_firestore_records_btn", disabled=not sources_ready):
            try:
                rows = _fetch_firestore_posts(status_filter=status_filter, limit=load_limit,
                                             source_urls=analysis_sources)
                st.session_state["firestore_rows"] = rows
                st.session_state.pop("analysis_record_selection", None)
                st.session_state["firestore_load_error"] = ""
                st.session_state["firestore_last_load_count"] = len(rows)
            except Exception as exc:
                st.session_state["firestore_rows"] = []
                st.session_state["firestore_load_error"] = str(exc).strip() or repr(exc)
                st.session_state["firestore_last_load_count"] = None

        firestore_rows = st.session_state.get("firestore_rows", [])
        firestore_load_error = st.session_state.get("firestore_load_error", "")
        firestore_last_load_count = st.session_state.get("firestore_last_load_count", None)
        if firestore_load_error:
            st.error(f"Load failed: {firestore_load_error}")
        elif firestore_last_load_count is not None:
            if firestore_last_load_count == 0:
                st.info(
                    "No saved records match these sources and status. "
                    "Collect posts from your current Facebook sources or r/cambodia and save the results, "
                    "or search Online News and save its articles. Then load records here."
                )
            else:
                st.success(f"Loaded {firestore_last_load_count} records from Firestore.")

        if firestore_rows:
            display_rows = []
            for row in firestore_rows:
                display_rows.append(
                    {
                        "_id": row.get("_id", ""),
                        "status": row.get("status", ""),
                        "custom_title": row.get("custom_title", ""),
                        "url": row.get("url", ""),
                        "scraped_at": row.get("scraped_at", ""),
                        "raw_text_preview": str(row.get("raw_text", ""))[:160],
                    }
                )
            st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

            id_options = [str(r.get("_id", "")) for r in firestore_rows if r.get("_id") and is_analyzable(r)]
            selected_ids = st.multiselect(
                "Select records to process",
                options=id_options,
                default=id_options,
                key="analysis_record_selection",
            )

            if st.button(f"Run {provider_label} on selected records", key="run_analysis_selected_btn",
                         disabled=not get_ai_key(provider)):
                if not selected_ids:
                    st.error("Please select at least one record.")
                else:
                    selected_posts = [r for r in firestore_rows if str(r.get("_id", "")) in set(selected_ids)]
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
                        status_container.update(
                            label=(
                                f"Done — {summary['processed']} processed, "
                                f"{summary['error']} error(s)"
                            ),
                            state="error" if summary["error"] else "complete",
                        )
                    except Exception as exc:
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

        # --- Display analysis results ---
        analysis_results = st.session_state.get("analysis_last_results")
        if analysis_results:
            st.divider()
            st.subheader("Analysis results")

            res_c1, res_c2 = st.columns(2)
            res_c1.metric("Processed", analysis_results.get("processed", 0))
            res_c2.metric("Errors", analysis_results.get("error", 0))

            for message in dict.fromkeys(analysis_results.get("errors", [])):
                st.error(message)
            analyses = analysis_results.get("analyses", [])
            if analyses:
                visible = _render_signal_overview(analyses, "latest_signals_filter")
                _render_analysis_cards(visible)
                with st.expander("Raw analysis JSON"):
                    st.json(analyses)
            elif analysis_results.get("error", 0) > 0:
                st.warning("No records were analyzed. Resolve the error above and retry."
                           if analysis_results.get("errors") else
                           "This result was created before detailed errors were available. Run analysis again to see the cause.")

        # --- View previously processed records ---
        st.divider()
        st.subheader("View processed records")
        st.caption("Load records with status 'processed' to see their saved analysis.")
        if st.button("Load processed records", key="load_processed_records_btn", disabled=not sources_ready):
            try:
                processed_rows = _fetch_firestore_posts(status_filter="processed", limit=load_limit,
                                                       source_urls=analysis_sources)
                st.session_state["processed_rows"] = processed_rows
            except Exception as exc:
                st.error(f"Failed to load: {str(exc).strip() or repr(exc)}")
                st.session_state["processed_rows"] = []

        processed_rows = st.session_state.get("processed_rows", [])
        _render_processed_deletion(processed_rows)
        if processed_rows:
            _render_processed_records(processed_rows)

    # Render after processing so the trends page includes the latest saved run.
    with trends_tab:
        _render_sentiment_history(source_urls=analysis_sources)
        _render_news_risk_history()

    with gencite_tab:
        render_gencite_workspace(user_email)


if __name__ == "__main__":
    main()

