"""Frontend-first Streamlit dashboard for Project Sentinel."""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from modules.database import init_db, mark_post_processed, upsert_post
from modules.processor import analyze_posts_with_gemini
from modules.scraper import scrape_group

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
    query = db.collection(os.getenv("FIREBASE_COLLECTION", "raw_posts")).limit(limit)
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


def _run_multi_scrape_sync(urls: List[str], headless: bool, scroll_times: int) -> List[Dict[str, Any]]:
    if sys.platform.startswith("win") and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    async def _runner() -> List[Dict[str, Any]]:
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

        tasks = [scrape_one(url) for url in urls]
        batches = await asyncio.gather(*tasks)
        merged: List[Dict[str, Any]] = []
        for batch in batches:
            merged.extend(batch)
        return merged

    return asyncio.run(_runner())


def _capture_facebook_storage_state(
    storage_state_path: str,
    timeout_seconds: int = 240,
) -> str:
    """
    Open an interactive browser and save Playwright storage state
    when Facebook login is detected.
    """
    if sys.platform.startswith("win") and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    from playwright.async_api import async_playwright

    if sys.platform.startswith("win") and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    output = Path(storage_state_path or "storage_state.json").expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    async def _runner() -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome", headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

            deadline = time.time() + timeout_seconds
            logged_in = False
            while time.time() < deadline:
                cookies = await context.cookies()
                if any(
                    cookie.get("name") == "c_user" and "facebook.com" in cookie.get("domain", "")
                    for cookie in cookies
                ):
                    logged_in = True
                    break
                await page.wait_for_timeout(1000)

            if not logged_in:
                await browser.close()
                raise RuntimeError(
                    "Facebook login not detected before timeout. "
                    "Please log in in the opened browser, then retry."
                )

            await context.storage_state(path=str(output))
            await browser.close()
        return str(output)

    return asyncio.run(_runner())


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


def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="Project Sentinel", layout="wide")
    scrape_tab, gemini_tab = st.tabs(["Scrap data", "Gemini processing"])

    with scrape_tab:
        st.caption("Use this to test scraper output directly from the dashboard.")
        with st.expander("log into facebook", expanded=False):
            st.caption("Essential step for scrapping facebook posts")
            st.caption(
                "Capture authenticated Playwright session. A Chrome window opens; "
                "log in to Facebook there. Session saves automatically once login is detected."
            )
            if st.button("Capture Facebook Login Session", key="capture_fb_session_btn"):
                with st.spinner("Waiting for Facebook login in opened browser..."):
                    try:
                        auth_state_path = os.getenv("PLAYWRIGHT_STORAGE_STATE", AUTH_STORAGE_STATE_FILE).strip()
                        saved_path = _capture_facebook_storage_state(
                            storage_state_path=auth_state_path or AUTH_STORAGE_STATE_FILE,
                            timeout_seconds=AUTH_LOGIN_TIMEOUT_SECONDS,
                        )
                        os.environ["PLAYWRIGHT_STORAGE_STATE"] = saved_path
                        st.toast("Facebook auth session captured.")
                    except Exception as exc:
                        error_text = str(exc).strip() or repr(exc)
                        st.error(f"Auth capture failed: {error_text}")
                        with st.expander("Auth Error Details"):
                            st.code(traceback.format_exc())

        urls_text = st.text_area(
            "Paste up to 5 links (one per line)",
            value="https://sg.news.yahoo.com/bangladesh-pm-tarique-rahman-lawmakers-052056107.html",
            height=140,
        )
        input_urls = [line.strip() for line in urls_text.splitlines() if line.strip()]
        unique_urls: List[str] = []
        seen = set()
        for item in input_urls:
            if item in seen:
                continue
            seen.add(item)
            unique_urls.append(item)
        target_urls = unique_urls[:5]
        if len(unique_urls) > 5:
            st.warning("Only first 5 links will be used in one run.")

        storage_state_path = os.getenv("PLAYWRIGHT_STORAGE_STATE", "").strip()
        if any("facebook.com" in u.lower() for u in target_urls) and not storage_state_path:
            st.warning(
                "Facebook usually requires authenticated session context. "
                "Set PLAYWRIGHT_STORAGE_STATE in .env for better results."
            )
        tc1, tc2 = st.columns(2)
        use_headless = tc1.checkbox("Headless Browser", value=True)
        scroll_times = tc2.slider("Scroll Iterations", min_value=1, max_value=50, value=10)

        if st.button("Run Scrape Test", type="primary"):
            if not target_urls:
                st.error("Please provide at least one URL.")
            else:
                st.session_state["last_scrape_urls"] = target_urls
                st.session_state["last_scrape_error"] = ""
                with st.spinner("Running scraper..."):
                    try:
                        results = _run_multi_scrape_sync(
                            urls=target_urls,
                            headless=use_headless,
                            scroll_times=scroll_times,
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
                st.write("**Last target URLs:**")
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
        st.subheader("Gemini processing")
        st.caption("Select Firestore records, run analysis with Gemini, and view results.")

        # --- Settings row ---
        gc1, gc2 = st.columns([1, 1])
        status_filter = gc1.selectbox("Record status", options=["pending", "processed", "error", "all"], index=0)
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

