"""Browse and delete reviewed selections from the shared source-record library."""

from math import ceil

import pandas as pd
import streamlit as st

from modules.analysis_library import dataset_options, select_records
from modules.client_report import platform_label
from modules.config import get_setting
from modules.database import delete_records, init_db, load_record_catalog
from modules.record_state import purge_record_caches


def source_label(row: dict) -> str:
    kind = str(row.get("source_type") or "")
    if kind == "website_page":
        return "Custom website"
    if kind == "news_article":
        return "Online news"
    if kind.startswith("reddit_"):
        return "Reddit"
    if kind in {"x_post", "x_reply"}:
        return "X"
    return platform_label(row)


def filter_records(rows: list[dict], dataset: str | None, platform: str | None,
                   status: str | None, search: str) -> list[dict]:
    words = search.casefold().split()
    return [row for row in select_records(rows, source_urls=None, limit=len(rows), dataset=dataset)
            if (platform is None or source_label(row) == platform)
            and (status is None or str(row.get("status") or "unknown") == status)
            and all(word in " ".join(str(row.get(field) or "") for field in
                                     ("custom_title", "title", "url", "_id")).casefold() for word in words)]


def _table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Record": row.get("title") or row.get("url") or row["_id"],
        "Dataset": str(row.get("custom_title") or "").strip() or "Untitled (older records)",
        "Source": source_label(row), "Status": row.get("status") or "unknown",
        "URL": row.get("url", ""), "Collected": str(row.get("scraped_at") or row.get("saved_at") or ""),
        "ID": row["_id"],
    } for row in rows])


def _clear_selection() -> None:
    # Explicit assignment also clears the browser widget; deleting the key only
    # clears server state and can leave the old selection visibly selected.
    st.session_state["record_manager_selection"] = []
    st.session_state.pop("record_manager_pending", None)


def render_record_management() -> None:
    st.subheader("Manage records")
    st.caption("Find and delete saved posts, articles and website pages across all analysis statuses.")
    st.info("This library is shared by approved users. Deleting a record also removes its saved analysis for everyone. "
            "Timeline summaries, saved source links, GenCite analyses and downloaded exports are kept.")
    collection = get_setting("FIREBASE_COLLECTION", "raw_posts") or "raw_posts"
    if st.session_state.get("record_manager_collection") != collection:
        st.session_state.pop("record_manager_rows", None)
        _clear_selection()
        st.session_state["record_manager_collection"] = collection
    if st.button("Load / refresh records", key="record_manager_refresh", type="primary"):
        _clear_selection()
        st.session_state.pop("record_manager_error", None)
        try:
            with st.spinner("Loading saved records…"):
                st.session_state["record_manager_rows"] = load_record_catalog(init_db())
        except Exception:
            st.session_state.pop("record_manager_rows", None)
            st.session_state["record_manager_error"] = "Records could not be loaded. Check the connection and try Load / refresh records again."
    notice = st.session_state.pop("record_manager_notice", None)
    if notice:
        st.success(f"Deleted {len(notice['deleted'])} record(s).")
        if notice["skipped"]:
            st.warning(f"Kept {len(notice['skipped'])} record(s) changed since loading. Refresh to review their current versions.")
        if notice["missing"]:
            st.info(f"{len(notice['missing'])} record(s) had already been deleted.")
    if st.session_state.get("record_manager_error"):
        st.error(st.session_state["record_manager_error"])
    if "record_manager_rows" not in st.session_state:
        st.caption("Load the library to browse records. Nothing is selected or deleted automatically.")
        return
    rows = st.session_state["record_manager_rows"]
    if not rows:
        st.info("No saved records to manage. Collect and save data, or refresh to check for newly saved records.")
        return

    groups = {group["key"]: group for group in dataset_options(rows, None)}
    platforms = sorted({source_label(row) for row in rows})
    statuses = sorted({str(row.get("status") or "unknown") for row in rows})
    # Keep the user's scope after deletion, including an emptied dataset. A
    # changing option list must never silently widen the next selection to all data.
    prior_dataset = st.session_state.get("record_manager_dataset")
    if isinstance(prior_dataset, str) and prior_dataset not in groups:
        groups[prior_dataset] = {"title": prior_dataset.removeprefix("title:") if prior_dataset.startswith("title:")
                                else "Untitled (older records)"}
    groups = dict(sorted(groups.items()))
    for key, options in (("record_manager_dataset", groups), ("record_manager_platform", platforms),
                         ("record_manager_status", statuses)):
        current = st.session_state.get(key)
        if current is not None:
            if isinstance(options, list) and current not in options:
                options.append(current)
            # Writing the value preserves it when Streamlit recreates a widget
            # whose options changed; relying on the old widget state resets it.
            st.session_state[key] = current
    first, second, third = st.columns(3)
    dataset = first.selectbox("Dataset", [None, *groups], key="record_manager_dataset", placeholder="All datasets",
                              format_func=lambda key: "All datasets" if key is None else
                              groups[key]["title"])
    platform = second.selectbox("Source", [None, *platforms], key="record_manager_platform", placeholder="All sources",
                                format_func=lambda value: value or "All sources")
    status = third.selectbox("Status", [None, *statuses], key="record_manager_status", placeholder="All statuses",
                             format_func=lambda value: value or "All statuses")
    search = st.text_input("Search records", key="record_manager_search", max_chars=200,
                           placeholder="Dataset, page title, URL or record ID")
    per_page = st.selectbox("Records per page", [25, 50, 100, 200], index=1, key="record_manager_page_size")
    context = (collection, dataset, platform, status, search, per_page)
    if st.session_state.get("record_manager_context") != context:
        _clear_selection()
        st.session_state["record_manager_page"] = 1
        st.session_state["record_manager_context"] = context
    matching = filter_records(rows, dataset, platform, status, search)
    pages = list(range(1, max(1, ceil(len(matching) / per_page)) + 1))
    if st.session_state.get("record_manager_page") not in pages:
        st.session_state["record_manager_page"] = 1
    page = st.selectbox("Page", pages, key="record_manager_page", format_func=lambda p: f"{p} of {len(pages)}")
    visible = matching[(page - 1) * per_page:page * per_page]
    # Paging, refreshing and changing filters invalidate a previous deletion review.
    view = (context, page, tuple((row["_id"], row["_version"]) for row in visible))
    if st.session_state.get("record_manager_view") != view:
        _clear_selection()
        st.session_state["record_manager_view"] = view
    st.caption(f"{len(rows)} saved records · {len(matching)} match your filters · {len(visible)} on this page")
    if not visible:
        st.info("No records match. Try another dataset, source, status or search.")
        return
    st.dataframe(_table(visible), hide_index=True, width="stretch")
    options = {row["_id"]: row for row in visible}
    if "record_manager_selection" in st.session_state:
        st.session_state["record_manager_selection"] = [ident for ident in st.session_state["record_manager_selection"]
                                                        if ident in options]
    select, clear = st.columns(2)
    select.button("Select all records on this page", key="record_manager_select_all",
                  on_click=lambda: st.session_state.update(record_manager_selection=list(options)))
    clear.button("Clear selection", key="record_manager_clear", on_click=_clear_selection)
    selected = st.multiselect("Records to delete", list(options), default=[], key="record_manager_selection",
                              format_func=lambda ident: f"{str(options[ident].get('title') or options[ident].get('url') or ident)[:100]} · {ident[:8]}")
    # Recheck the membership even if widget state contains IDs from another page.
    selected_rows = [options[ident] for ident in selected if ident in options]
    versions = {row["_id"]: row["_version"] for row in selected_rows}
    st.caption(f"{len(versions)} record(s) selected. Select all applies only to this page, up to 200 records per deletion.")
    if st.session_state.get("record_manager_pending") != versions:
        st.session_state.pop("record_manager_pending", None)
    if st.button(f"Review deletion of {len(versions)} record(s)", key="record_manager_review", disabled=not versions):
        st.session_state["record_manager_pending"] = versions.copy()
    pending = st.session_state.get("record_manager_pending")
    if pending:
        with st.container(border=True):
            st.subheader("Review deletion")
            st.warning(f"Permanently delete these {len(pending)} record(s) and their saved analyses? "
                       "This affects all users and cannot be undone. Records changed since loading will be kept.")
            st.dataframe(_table(selected_rows), hide_index=True, width="stretch")
            confirm, cancel = st.columns(2)
            if confirm.button(f"Permanently delete {len(pending)} record(s)", key="record_manager_confirm", type="primary"):
                try:
                    with st.spinner("Deleting selected records…"):
                        result = delete_records(init_db(), pending)
                except Exception:
                    purge_record_caches(st.session_state, {"deleted": [], "missing": [], "skipped": list(pending)})
                    st.session_state.pop("record_manager_rows", None)
                    st.session_state["record_manager_error"] = (
                        "Deletion could not be confirmed. Load / refresh records to check the library before trying again.")
                else:
                    purge_record_caches(st.session_state, result)
                    if result["skipped"]:
                        st.session_state.pop("record_manager_rows", None)
                    st.session_state["record_manager_notice"] = result
                st.session_state.pop("record_manager_pending", None)
                st.rerun()
            if cancel.button("Cancel deletion", key="record_manager_cancel"):
                st.session_state.pop("record_manager_pending", None)
                st.rerun()
