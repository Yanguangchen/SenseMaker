"""Frontend-first Streamlit dashboard for Project Sentinel."""

from __future__ import annotations

import asyncio
import html
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from modules.auth import require_access
from modules.config import get_setting
from modules.database import init_db, mark_post_processed, upsert_post
from modules.facebook_session import (
    FacebookSession,
    capture_facebook_session,
    check_facebook_session,
    saved_facebook_user_id,
)
from modules.processor import analyze_posts_with_gemini
from modules.saved_links import library_owner_id, load_saved_links, parse_links, remove_saved_links, save_links

try:
    from modules.scraper import scrape_group
    SCRAPER_AVAILABLE = True
except Exception:
    SCRAPER_AVAILABLE = False

IS_CLOUD = os.path.exists("/mount/src") or os.getenv("SENSEMAKER_HOSTED") == "1"

AUTH_STORAGE_STATE_FILE = "storage_state.json"
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
        if not url:
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


def _fetch_firestore_posts(status_filter: str, limit: int) -> List[Dict[str, Any]]:
    db = init_db()
    query = db.collection(get_setting("FIREBASE_COLLECTION", "raw_posts")).limit(limit)
    if status_filter != "all":
        query = query.where("status", "==", status_filter)
    docs = query.stream()
    rows: List[Dict[str, Any]] = []
    for doc in docs:
        item = doc.to_dict() or {}
        item["_id"] = item.get("_id") or doc.id
        rows.append(item)
    return rows


def _process_selected_firestore_posts(
    selected_posts: List[Dict[str, Any]],
    gemini_key: str,
    status_callback: Any = None,
) -> Dict[str, Any]:
    """Run Gemini on selected posts and persist results to Firestore.

    Returns dict with 'processed', 'error' counts and 'analyses' list.
    """
    db = init_db()
    model_name = st.session_state.get("gemini_model_name", "models/gemini-2.0-flash")
    analysis_by_id = analyze_posts_with_gemini(
        selected_posts,
        api_key=gemini_key,
        model_name=model_name,
        on_status=status_callback,
    )
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
            mark_post_processed(db, doc_id=doc_id, analysis=analysis, status="processed")
            processed += 1
            analyses.append({
                "_id": doc_id,
                "url": post.get("url", ""),
                "raw_text_preview": str(post.get("raw_text", ""))[:200],
                "translation": analysis.get("translation", ""),
                "sentiment": analysis.get("sentiment", ""),
                "risk_score": analysis.get("risk_score", ""),
                "topics": ", ".join(analysis.get("topics", [])) if isinstance(analysis.get("topics"), list) else str(analysis.get("topics", "")),
            })
        else:
            mark_post_processed(db, doc_id=doc_id, analysis=None, status="error")
            errored += 1
    return {"processed": processed, "error": errored, "analyses": analyses}


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


def _run_multi_scrape_sync(
    urls: List[str], headless: bool, scroll_times: int,
    concurrency: int = 3,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> List[Dict[str, Any]]:
    if sys.platform.startswith("win") and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    async def _runner() -> List[Dict[str, Any]]:
        semaphore = asyncio.Semaphore(max(1, concurrency))
        completed = 0

        async def scrape_one(url: str) -> List[Dict[str, Any]]:
            try:
                rows = await scrape_group(
                    target_url=url,
                    headless=headless,
                    scroll_min=scroll_times,
                    scroll_max=scroll_times,
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

        tasks = [bounded_scrape(url) for url in urls]
        batches = await asyncio.gather(*tasks)
        merged: List[Dict[str, Any]] = []
        for batch in batches:
            merged.extend(batch)
        return merged

    return asyncio.run(_runner())


@st.cache_data(ttl=300, show_spinner=False)
def _check_facebook_session(path: str, modified: int, size: int) -> FacebookSession:
    # Recheck whenever login saves a new file, and at least every five minutes.
    return check_facebook_session(path)


def _get_facebook_session(path: str) -> FacebookSession:
    if not saved_facebook_user_id(path):
        return FacebookSession()
    if IS_CLOUD:
        return FacebookSession(status="unavailable")
    try:
        info = Path(path).stat()
    except OSError:
        return FacebookSession()
    with st.spinner("Checking Facebook connection…"):
        return _check_facebook_session(path, info.st_mtime_ns, info.st_size)


def _render_facebook_connection() -> FacebookSession:
    path = str(Path(os.getenv("PLAYWRIGHT_STORAGE_STATE", "").strip() or AUTH_STORAGE_STATE_FILE).expanduser().resolve())
    session = _get_facebook_session(path)
    with st.container(border=True, key="facebook_connection"):
        identity, action = st.columns([3, 1], vertical_alignment="center")
        if session.status == "connected":
            os.environ["PLAYWRIGHT_STORAGE_STATE"] = path
            name = session.name or f"Facebook account {session.user_id}"
            initials = "".join(word[0] for word in name.split()[:2])
            identity.markdown(
                f'<div class="fb-identity"><div class="fb-avatar">{html.escape(initials)}</div>'
                '<div><p class="fb-label">FACEBOOK</p>'
                f'<p class="fb-name">{html.escape(name)}</p>'
                '<p class="fb-detail">Ready to collect posts.</p></div></div>',
                unsafe_allow_html=True,
            )
            action.markdown('<span class="fb-status">Connected</span>', unsafe_allow_html=True)
        else:
            identity.markdown(
                '<div class="fb-identity"><div class="fb-avatar" aria-hidden="true">f</div>'
                '<div><p class="fb-label">FACEBOOK</p><p class="fb-name">Connect your account</p>'
                '<p class="fb-detail">Log in once to collect Facebook posts.</p></div></div>',
                unsafe_allow_html=True,
            )
            if action.button(
                "Log in to Facebook", key="capture_fb_session_btn", type="primary",
                use_container_width=True, disabled=IS_CLOUD,
            ):
                with st.spinner("Finish logging in in the browser window…"):
                    try:
                        capture_facebook_session(path, timeout_seconds=AUTH_LOGIN_TIMEOUT_SECONDS)
                        os.environ["PLAYWRIGHT_STORAGE_STATE"] = path
                        _check_facebook_session.clear()
                        st.rerun()
                    except Exception:
                        st.error("Facebook login did not finish. Please try again.")
            if IS_CLOUD:
                st.caption("Log in from SenseMaker on your computer to collect Facebook posts.")
            elif session.status == "unavailable":
                st.caption("We couldn’t confirm your saved session. Log in again to reconnect.")
    return session


def _render_link_workspace(user_email: str) -> tuple[List[str], bool]:
    owner = library_owner_id(user_email)
    library_key = f"saved_links_{owner}"
    selection_key = f"selected_links_{owner}"
    pending_key = f"next_selected_links_{owner}"
    input_key = f"new_links_{owner}"
    clear_key = f"clear_new_links_{owner}"
    message_key = f"links_message_{owner}"

    def refresh() -> None:
        try:
            with st.spinner("Loading saved links…"):
                urls = load_saved_links(init_db(), user_email)
            st.session_state[library_key] = {"urls": urls, "error": False}
        except Exception:
            previous = st.session_state.get(library_key, {})
            st.session_state[library_key] = {"urls": previous.get("urls", []), "error": True}

    if library_key not in st.session_state:
        refresh()
    library = st.session_state[library_key]
    saved = library["urls"]
    saved_set = set(saved)
    if pending_key in st.session_state:
        st.session_state[selection_key] = st.session_state.pop(pending_key)
    # A refresh may include removals made on another device.
    if selection_key in st.session_state:
        st.session_state[selection_key] = [url for url in st.session_state[selection_key] if url in saved_set]
    if st.session_state.pop(clear_key, False):
        st.session_state[input_key] = ""

    with st.container(border=True, key="link_workspace"):
        st.subheader("Your links")
        st.caption("Save sources once, then choose them whenever you want to collect posts.")
        if message_key in st.session_state:
            st.success(st.session_state.pop(message_key))
        if library["error"]:
            st.warning("Saved links couldn’t be loaded. Refresh to try again, or paste links below.")

        selected = st.multiselect(
            f"Saved links ({len(saved)})", options=saved, key=selection_key,
            placeholder="Search and choose saved links", disabled=not saved,
        )
        select_action, clear_action, refresh_action = st.columns(3)
        if select_action.button("Select all", key="select_all_links", use_container_width=True, disabled=not saved):
            st.session_state[pending_key] = saved.copy()
            st.rerun()
        if clear_action.button("Clear selection", key="clear_selected_links", use_container_width=True, disabled=not selected):
            st.session_state[pending_key] = []
            st.rerun()
        if refresh_action.button("Refresh", key="refresh_saved_links", use_container_width=True):
            refresh()
            st.rerun()

        if selected and st.button(
            f"Remove {len(selected)} saved link{'s' if len(selected) != 1 else ''}",
            key="remove_selected_links", use_container_width=True,
            help="Removes these links from your library. Collected posts are kept.",
        ):
            try:
                remove_saved_links(init_db(), user_email, selected)
                removed = set(selected)
                st.session_state[library_key]["urls"] = [url for url in saved if url not in removed]
                st.session_state[pending_key] = []
                st.session_state[message_key] = f"Removed {len(selected)} saved links."
                st.rerun()
            except Exception:
                st.error("Some links couldn’t be removed. Refresh your library and try again.")

        text = st.text_area(
            "Add links (one per line)", key=input_key, height=160,
            placeholder="https://www.facebook.com/groups/your-community\nhttps://example.com/news",
            help="Paste as many links as you need. You can collect them now or save them for later.",
        )
        pasted, invalid = parse_links(text)
        if invalid:
            st.error("Correct the invalid links before continuing. Use one http or https web link per line.")
            st.code("\n".join(invalid[:5]), language=None)
        save_clicked = st.button("Save links", key="save_source_links", use_container_width=True)
        if save_clicked and not pasted and not invalid:
            st.info("Add at least one link to save.")
        if save_clicked and pasted and not invalid:
            new_links = [url for url in pasted if url not in saved_set]
            try:
                if new_links:
                    save_links(init_db(), user_email, new_links)
                st.session_state[library_key]["urls"] = list(dict.fromkeys(saved + pasted))
                st.session_state[pending_key] = list(dict.fromkeys(selected + pasted))
                st.session_state[clear_key] = True
                st.session_state[message_key] = (
                    f"Saved {len(new_links)} new links. Your links are selected and ready to collect."
                    if new_links else "These links are already saved. They’re selected and ready to collect."
                )
                st.rerun()
            except Exception:
                st.error("Some links couldn’t be saved. Refresh your library and retry; saved links are kept.")

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
    """Display Gemini analysis results as visual cards."""
    for item in analyses:
        sentiment = item.get("sentiment", "Unknown")
        risk = item.get("risk_score", "?")
        topics = item.get("topics", "")
        translation = item.get("translation", "")
        preview = item.get("raw_text_preview", "")
        url = item.get("url", "")
        sent_color = SENTIMENT_COLORS.get(sentiment, "#888888")
        risk_col = _risk_color(risk)

        st.markdown(
            f"""
<div style="border:1px solid #ddd; border-radius:8px; padding:16px; margin-bottom:12px;">
  <div style="display:flex; gap:12px; align-items:center; margin-bottom:8px;">
    <span style="background:{sent_color}; color:#fff; padding:4px 10px; border-radius:4px; font-weight:600;">
      {sentiment}
    </span>
    <span style="background:{risk_col}; color:#fff; padding:4px 10px; border-radius:4px; font-weight:600;">
      Risk: {risk}/10
    </span>
    <span style="color:#888; font-size:0.85em;">{topics}</span>
  </div>
  <div style="margin-bottom:6px;"><strong>Translation:</strong> {translation}</div>
  <div style="color:#666; font-size:0.85em;"><strong>Original:</strong> {preview}</div>
  {"<div style='margin-top:4px;'><a href='" + url + "' target='_blank' style='font-size:0.8em;'>Source</a></div>" if url else ""}
</div>
""",
            unsafe_allow_html=True,
        )


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
            "sentiment": a.get("sentiment", "Unknown"),
            "risk_score": a.get("risk_score", "?"),
            "topics": ", ".join(a.get("topics", [])) if isinstance(a.get("topics"), list) else str(a.get("topics", "")),
            "translation": a.get("translation", ""),
            "raw_text_preview": str(r.get("raw_text", ""))[:200],
            "url": r.get("url", ""),
        })
    with st.expander("Detailed analysis cards", expanded=True):
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
.stApp span,
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


def main() -> None:
    load_dotenv()
    _bridge_secrets_to_env()
    st.set_page_config(page_title="SenseMaker", layout="wide")
    _apply_ui_theme()
    user_email = require_access()
    st.title("SenseMaker")
    scrape_tab, gemini_tab = st.tabs(["Scrap data", "Sentiment Analysis"])

    with scrape_tab:
        facebook_session = _render_facebook_connection()

        target_urls, links_valid = _render_link_workspace(user_email)

        if any("facebook.com" in u.lower() for u in target_urls) and facebook_session.status != "connected":
            st.info("Connect your Facebook account above before collecting Facebook posts.")
        tc1, tc2 = st.columns(2)
        use_headless = tc1.checkbox("Headless Browser", value=True, disabled=IS_CLOUD)
        scroll_times = tc2.slider("Scroll Iterations", min_value=1, max_value=50, value=10)

        if st.button("Collect posts", type="primary", use_container_width=True):
            if not links_valid:
                st.error("Correct the invalid links above before collecting posts.")
            elif not target_urls:
                st.error("Please provide at least one URL.")
            else:
                st.session_state["last_scrape_urls"] = target_urls
                st.session_state["last_scrape_error"] = ""
                progress = st.progress(0, text=f"Starting collection for {len(target_urls)} links…")
                with st.spinner("Running scraper..."):
                    try:
                        results = _run_multi_scrape_sync(
                            urls=target_urls,
                            headless=use_headless,
                            scroll_times=scroll_times,
                            on_progress=lambda done, total, _url: progress.progress(
                                done / total, text=f"Processed {done} of {total} links"
                            ),
                        )
                        st.session_state["last_scrape_results"] = results
                    except Exception as exc:
                        st.session_state["last_scrape_results"] = []
                        error_text = str(exc).strip() or repr(exc)
                        st.session_state["last_scrape_error"] = error_text
                        st.error(f"Scrape failed: {error_text}")

        scrape_results = st.session_state.get("last_scrape_results", None)
        scrape_urls = st.session_state.get("last_scrape_urls", [])
        scrape_error = st.session_state.get("last_scrape_error", "")
        if scrape_results is not None:
            if scrape_urls:
                with st.expander(f"Sources in the last collection ({len(scrape_urls)})"):
                    st.code("\n".join(scrape_urls))
            if scrape_error:
                st.caption(f"Last error: {scrape_error}")
            st.write(f"**Posts found:** {len(scrape_results)}")
            if scrape_results:
                custom_title = st.text_input(
                    "Custom title",
                    value=st.session_state.get("custom_title_value", ""),
                    key="custom_title_value",
                    help="This title will be saved on each object uploaded to Firestore.",
                )
                if st.button("Save data to cloud", key="save_last_results_btn"):
                    try:
                        if not custom_title.strip():
                            st.error("Please enter a custom title before saving.")
                        else:
                            write_summary = _write_results_to_firestore(scrape_results, custom_title=custom_title)
                            st.success(
                                "Saved to Firestore: "
                                f"{write_summary['inserted']} posts "
                                f"(skipped: {write_summary['skipped']})."
                            )
                    except Exception as exc:
                        error_text = str(exc).strip() or repr(exc)
                        st.error(f"Firestore write failed: {error_text}")
                st.dataframe(pd.DataFrame(scrape_results), use_container_width=True, hide_index=True)
                with st.expander("Raw JSON Output"):
                    st.json(scrape_results)
            else:
                st.info("No posts detected for this URL with current selectors.")

    with gemini_tab:
        st.subheader("Sentiment Analysis")
        st.caption("Select Firestore records, run analysis with Gemini, and view results.")

        # --- Settings row ---
        gc1, gc2 = st.columns([1, 1])
        status_filter = gc1.selectbox("Record status", options=["all", "pending", "processed", "error"], index=0)
        load_limit = gc2.slider("Load limit", min_value=1, max_value=200, value=50)
        gemini_key_input = st.text_input(
            "Gemini API key",
            value=os.getenv("GEMINI_KEY", ""),
            type="password",
            help="Uses this key for Gemini analysis.",
        )
        st.text_input(
            "Gemini model",
            value=os.getenv("GEMINI_MODEL", "models/gemini-2.0-flash"),
            key="gemini_model_name",
            help="Model identifier, e.g. models/gemini-2.0-flash.",
        )

        # --- Load records ---
        if st.button("Load records from Firestore", key="load_firestore_records_btn"):
            try:
                rows = _fetch_firestore_posts(status_filter=status_filter, limit=load_limit)
                st.session_state["firestore_rows"] = rows
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
                    "No records found for this filter. "
                    "Scrape and save data first, or switch status filter to 'all'."
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

            id_options = [str(r.get("_id", "")) for r in firestore_rows if r.get("_id")]
            selected_ids = st.multiselect(
                "Select records to process",
                options=id_options,
                default=id_options,
            )

            if st.button("Run Gemini on selected records", key="run_gemini_selected_btn"):
                if not selected_ids:
                    st.error("Please select at least one record.")
                elif not gemini_key_input.strip():
                    st.error("Please provide a Gemini API key.")
                else:
                    selected_posts = [r for r in firestore_rows if str(r.get("_id", "")) in set(selected_ids)]
                    status_container = st.status(
                        f"Running Gemini on {len(selected_posts)} record(s)...",
                        expanded=True,
                    )
                    try:
                        def _update_status(msg: str) -> None:
                            status_container.write(msg)

                        summary = _process_selected_firestore_posts(
                            selected_posts,
                            gemini_key=gemini_key_input,
                            status_callback=_update_status,
                        )
                        st.session_state["gemini_last_results"] = summary
                        status_container.update(
                            label=(
                                f"Done — {summary['processed']} processed, "
                                f"{summary['error']} error(s)"
                            ),
                            state="complete",
                        )
                    except Exception as exc:
                        error_text = str(exc).strip() or repr(exc)
                        status_container.update(
                            label=f"Failed: {error_text}",
                            state="error",
                        )
                        with st.expander("Error details"):
                            st.code(traceback.format_exc())

        # --- Display Gemini results ---
        gemini_results = st.session_state.get("gemini_last_results")
        if gemini_results:
            st.divider()
            st.subheader("Analysis results")

            res_c1, res_c2 = st.columns(2)
            res_c1.metric("Processed", gemini_results.get("processed", 0))
            res_c2.metric("Errors", gemini_results.get("error", 0))

            analyses = gemini_results.get("analyses", [])
            if analyses:
                _render_analysis_cards(analyses)
                with st.expander("Raw analysis JSON"):
                    st.json(analyses)
            elif gemini_results.get("error", 0) > 0:
                st.warning(
                    "No analysis could be extracted. This usually means Gemini could not "
                    "match posts back. Try processing fewer records or check the raw text."
                )

        # --- View previously processed records ---
        st.divider()
        st.subheader("View processed records")
        st.caption("Load records with status 'processed' to see their saved analysis.")
        if st.button("Load processed records", key="load_processed_records_btn"):
            try:
                processed_rows = _fetch_firestore_posts(status_filter="processed", limit=load_limit)
                st.session_state["processed_rows"] = processed_rows
            except Exception as exc:
                st.error(f"Failed to load: {str(exc).strip() or repr(exc)}")
                st.session_state["processed_rows"] = []

        processed_rows = st.session_state.get("processed_rows", [])
        if processed_rows:
            _render_processed_records(processed_rows)


if __name__ == "__main__":
    main()

