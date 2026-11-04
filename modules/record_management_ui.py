"""Browse and delete reviewed selections from the shared source-record library."""

from datetime import datetime
from math import ceil

import pandas as pd
import streamlit as st

from modules.analysis_library import dataset_date_label, dataset_key, dataset_options, select_records
from modules.client_report import platform_label
from modules.config import get_setting
from modules.database import delete_dataset_records, delete_records, init_db, load_record_catalog
from modules.dataset_cards import apply_dataset_card_theme, featured_datasets, render_dataset_cards, viewer_timezone
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


def _clear_deletion_reviews() -> None:
    for key in ("record_manager_pending", "record_manager_row_pending", "dataset_delete_pending"):
        st.session_state.pop(key, None)


def _clear_selection() -> None:
    # Explicit assignment also clears the browser widget; deleting the key only
    # clears server state and can leave the old selection visibly selected.
    st.session_state["record_manager_selection"] = []
    _clear_deletion_reviews()


def _select_page_records(ids: list[str]) -> None:
    _clear_selection()
    st.session_state["record_manager_selection"] = ids


def _review_row_deletion(ident: str, version: str) -> None:
    _clear_selection()
    st.session_state["record_manager_row_pending"] = {ident: version}


def _delete_reviewed_records(versions: dict[str, str]) -> None:
    try:
        with st.spinner("Deleting selected records…"):
            result = delete_records(init_db(), versions)
    except Exception:
        purge_record_caches(st.session_state, {"deleted": [], "missing": [], "skipped": list(versions)})
        st.session_state.pop("record_manager_rows", None)
        st.session_state["record_manager_error"] = (
            "Deletion could not be confirmed. Refresh datasets to check the library before trying again.")
    else:
        purge_record_caches(st.session_state, result)
        if result["skipped"]:
            st.session_state.pop("record_manager_rows", None)
            st.session_state["record_manager_needs_refresh"] = True
        st.session_state["record_manager_notice"] = result
    _clear_deletion_reviews()
    st.rerun()


def _render_record_rows(rows: list[dict]) -> None:
    st.subheader("Records")
    st.caption("Use Delete beside a record to review and remove that record.")
    with st.container(height=560 if len(rows) > 5 else "content", border=False):
        for row in rows:
            ident = row["_id"]
            versions = {ident: row["_version"]}
            with st.container(border=True):
                details, action = st.columns([5, 1], vertical_alignment="center")
                with details:
                    st.text(row.get("title") or row.get("url") or ident)
                    st.caption(f"{source_label(row)} · {row.get('status') or 'unknown'} · "
                               f"{str(row.get('custom_title') or '').strip() or 'Untitled (older records)'}")
                    if row.get("title") and row.get("url"):
                        st.text(row["url"])
                action.button("Delete", icon=":material/delete:", key=f"record_manager_delete_{ident}",
                              help="Review deletion of this record", on_click=_review_row_deletion,
                              args=(ident, row["_version"]))
                if st.session_state.get("record_manager_row_pending") == versions:
                    st.warning("Permanently delete this record and its saved analysis? "
                               "This affects all users and cannot be undone. If the record changed since loading, it will be kept.")
                    st.text(f"Record ID: {ident}")
                    confirm, cancel = st.columns(2)
                    if confirm.button("Permanently delete record", key=f"record_manager_row_confirm_{ident}", type="primary"):
                        _delete_reviewed_records(versions)
                    cancel.button("Cancel deletion", key=f"record_manager_row_cancel_{ident}",
                                  on_click=_clear_deletion_reviews)


def _open_dataset(dataset: str | None) -> None:
    _clear_selection()
    st.session_state.update(record_manager_details=True, record_manager_dataset=dataset,
                            record_manager_platform=None, record_manager_status=None,
                            record_manager_search="", record_manager_page=1)


def _back_to_datasets() -> None:
    _clear_selection()
    st.session_state["record_manager_details"] = False


def _render_dataset_cards(groups: list[dict], section: str, local_tz) -> None:
    render_dataset_cards(groups, section, local_tz, key_prefix="record_manager", on_select=_open_dataset)


def _render_dataset_browser(rows: list[dict], groups: list[dict]) -> None:
    local_tz = viewer_timezone()
    apply_dataset_card_theme()
    is_today, featured = featured_datasets(groups, datetime.now(local_tz))
    st.subheader("Today's datasets" if is_today else "Latest datasets")
    if is_today:
        st.caption(f"{len(featured)} datasets saved today · Newest first · Times in {local_tz}")
    else:
        st.caption(f"No datasets saved today. Showing the {len(featured)} latest datasets · Times in {local_tz}")
    _render_dataset_cards(featured, "featured", local_tz)
    st.divider()
    st.subheader("Find a dataset")
    search = st.text_input("Search datasets", key="record_manager_dataset_search", max_chars=200,
                           placeholder="Search by dataset name, page title, URL or record ID")
    if search.strip():
        keys = {dataset_key(row) for row in filter_records(rows, None, None, None, search)}
        matches = [group for group in groups if group["key"] in keys]
        st.caption(f"{len(matches)} matching datasets · {sum(group['count'] for group in matches):,} records")
        if not matches:
            st.info("No datasets match your search. Try a different dataset name, page title or URL.")
        else:
            if st.session_state.get("record_manager_dataset_query") != search:
                st.session_state["record_manager_dataset_page"] = 1
                st.session_state["record_manager_dataset_query"] = search
            pages = list(range(1, ceil(len(matches) / 12) + 1))
            if st.session_state.get("record_manager_dataset_page") not in pages:
                st.session_state["record_manager_dataset_page"] = 1
            page = st.selectbox("Search results page", pages, key="record_manager_dataset_page",
                                format_func=lambda value: f"{value} of {len(pages)}") if len(pages) > 1 else 1
            _render_dataset_cards(matches[(page - 1) * 12:page * 12], "search", local_tz)
    else:
        st.caption(f"Search across all {len(groups)} datasets in the shared library.")
    st.button("Browse all records", key="record_manager_browse_all", on_click=_open_dataset, args=(None,))


def _render_dataset_deletion(rows: list[dict], dataset: str | None) -> None:
    records = [row for row in rows if dataset is not None and dataset_key(row) == dataset]
    versions = {row["_id"]: row["_version"] for row in records}
    review = {"dataset": dataset, "versions": versions}
    if st.session_state.get("dataset_delete_pending") != review:
        st.session_state.pop("dataset_delete_pending", None)
    with st.expander("Delete an entire dataset"):
        if dataset is None:
            st.caption("Choose a single Dataset above to delete it in full.")
        else:
            st.caption(f"{len(records)} records across all pages, sources and statuses. "
                       "Source, status and search filters do not limit this action.")
        if st.button("Review dataset deletion", key="dataset_delete_review", disabled=not versions):
            _clear_selection()
            st.session_state["dataset_delete_pending"] = review
        if st.session_state.get("dataset_delete_pending") == review and versions:
            title = str(records[0].get("custom_title") or "").strip() or "Untitled (older records)"
            st.warning(f'Permanently delete dataset "{title}" and all {len(records)} reviewed records and their saved analyses? '
                       "This affects all users and cannot be undone. Changed records and newly added records are kept.")
            st.dataframe(_table(records), hide_index=True, width="stretch")
            confirm, cancel = st.columns(2)
            if confirm.button("Permanently delete dataset", key="dataset_delete_confirm", type="primary"):
                progress = st.progress(0, text="Deleting dataset…")
                try:
                    db = init_db()
                    result = delete_dataset_records(db, dataset, versions, on_progress=lambda done, total:
                        progress.progress(done / total, text=f"Checked {done} of {total} reviewed records"))
                except Exception:
                    result = {"deleted": [], "skipped": [], "missing": [], "unconfirmed": list(versions)}
                purge_record_caches(st.session_state, {**result, "skipped": result["skipped"] + result["unconfirmed"]})
                st.session_state.pop("record_manager_rows", None)
                st.session_state["record_manager_needs_refresh"] = True
                complete = False
                if not result["unconfirmed"]:
                    try:
                        fresh = load_record_catalog(db)
                        st.session_state["record_manager_rows"] = fresh
                        st.session_state.pop("record_manager_needs_refresh", None)
                        complete = not any(dataset_key(row) == dataset for row in fresh)
                    except Exception:
                        pass  # Deletion counts are known even if refreshing the catalog failed.
                st.session_state["dataset_delete_notice"] = {"title": title, "result": result, "complete": complete}
                if complete:
                    st.session_state["record_manager_removed_dataset"] = dataset
                st.session_state.pop("dataset_delete_pending", None)
                st.rerun()
            if cancel.button("Cancel dataset deletion", key="dataset_delete_cancel"):
                st.session_state.pop("dataset_delete_pending", None)
                st.rerun()


def render_record_management() -> None:
    st.subheader("Clean & manage data")
    st.caption("Browse your saved datasets. Open a card to review and manage its records.")
    st.caption("Shared library · Available to all approved users")
    # Preserve the search when its widget is temporarily hidden by dataset details.
    if "record_manager_dataset_search" in st.session_state:
        st.session_state["record_manager_dataset_search"] = st.session_state["record_manager_dataset_search"]
    collection = get_setting("FIREBASE_COLLECTION", "raw_posts") or "raw_posts"
    if st.session_state.get("record_manager_collection") != collection:
        st.session_state.pop("record_manager_rows", None)
        st.session_state.pop("record_manager_error", None)
        st.session_state.pop("record_manager_needs_refresh", None)
        _clear_selection()
        st.session_state["record_manager_collection"] = collection
    refresh = st.button("Refresh datasets", key="record_manager_refresh")
    if refresh or ("record_manager_rows" not in st.session_state
                   and not st.session_state.get("record_manager_needs_refresh")
                   and not st.session_state.get("record_manager_error")):
        _clear_selection()
        st.session_state.pop("record_manager_error", None)
        try:
            with st.spinner("Loading saved records…"):
                st.session_state["record_manager_rows"] = load_record_catalog(init_db())
            st.session_state.pop("record_manager_needs_refresh", None)
        except Exception:
            st.session_state.pop("record_manager_rows", None)
            st.session_state["record_manager_error"] = "Datasets could not be loaded. Check the connection and try Refresh datasets again."
    notice = st.session_state.pop("record_manager_notice", None)
    if notice:
        st.success(f"Deleted {len(notice['deleted'])} record(s).")
        if notice["skipped"]:
            st.warning(f"Kept {len(notice['skipped'])} record(s) changed since loading. Refresh to review their current versions.")
        if notice["missing"]:
            st.info(f"{len(notice['missing'])} record(s) had already been deleted.")
    if st.session_state.get("record_manager_error"):
        st.error(st.session_state["record_manager_error"])
    dataset_notice = st.session_state.pop("dataset_delete_notice", None)
    if dataset_notice:
        result = dataset_notice["result"]
        if dataset_notice["complete"]:
            st.success(f'Dataset "{dataset_notice["title"]}" deleted. Removed {len(result["deleted"])} record(s).')
        else:
            st.warning(f'Deleted {len(result["deleted"])} record(s) from "{dataset_notice["title"]}". '
                       "Dataset removal is incomplete or could not be verified. Refresh records before retrying.")
        if result["skipped"]:
            st.info(f'Kept {len(result["skipped"])} record(s) changed since loading or moved to another dataset.')
        if result["unconfirmed"]:
            st.error(f'Could not confirm deletion of {len(result["unconfirmed"])} remaining record(s). '
                     "Earlier confirmed deletions are included in the count above.")
    removed_dataset = st.session_state.pop("record_manager_removed_dataset", None)
    if removed_dataset and st.session_state.get("record_manager_dataset") == removed_dataset:
        st.session_state["record_manager_dataset"] = None
        _clear_selection()
    if "record_manager_rows" not in st.session_state:
        st.caption("Choose Refresh datasets to reload the library.")
        return
    rows = st.session_state["record_manager_rows"]
    if not rows:
        st.info("No saved records to manage. Collect and save data, or refresh to check for newly saved records.")
        return

    dataset_groups = dataset_options(rows, None)
    if not st.session_state.get("record_manager_details"):
        _render_dataset_browser(rows, dataset_groups)
        return
    st.button("Back to datasets", key="record_manager_back", on_click=_back_to_datasets)
    groups = {group["key"]: group for group in dataset_groups}
    current_dataset = st.session_state.get("record_manager_dataset")
    st.subheader(groups.get(current_dataset, {}).get("title", "Dataset records" if current_dataset else "All records"))
    st.caption("Filter and review records below before deleting anything.")
    st.info("This library is shared by approved users. Deleting a record also removes its saved analysis for everyone. "
            "Timeline summaries, saved source links, GenCite analyses and downloaded exports are kept.")
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
                              f"{groups[key]['title']} · {dataset_date_label(groups[key])}")
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
    _render_dataset_deletion(rows, dataset)
    if not visible:
        st.info("No records match. Try another dataset, source, status or search.")
        return
    _render_record_rows(visible)
    with st.expander("View record table"):
        st.dataframe(_table(visible), hide_index=True, width="stretch")
    st.subheader("Delete multiple records")
    options = {row["_id"]: row for row in visible}
    if "record_manager_selection" in st.session_state:
        st.session_state["record_manager_selection"] = [ident for ident in st.session_state["record_manager_selection"]
                                                        if ident in options]
    select, clear = st.columns(2)
    select.button("Select all records on this page", key="record_manager_select_all",
                  on_click=_select_page_records, args=(list(options),))
    clear.button("Clear selection", key="record_manager_clear", on_click=_clear_selection)
    selected = st.multiselect("Records to delete", list(options), default=[], key="record_manager_selection",
                              on_change=_clear_deletion_reviews,
                              format_func=lambda ident: f"{str(options[ident].get('title') or options[ident].get('url') or ident)[:100]} · {ident[:8]}")
    # Recheck the membership even if widget state contains IDs from another page.
    selected_rows = [options[ident] for ident in selected if ident in options]
    versions = {row["_id"]: row["_version"] for row in selected_rows}
    st.caption(f"{len(versions)} record(s) selected. Select all applies only to this page, up to 200 records per deletion.")
    if st.session_state.get("record_manager_pending") != versions:
        st.session_state.pop("record_manager_pending", None)
    if st.button(f"Review deletion of {len(versions)} record(s)", key="record_manager_review", disabled=not versions,
                 on_click=_clear_deletion_reviews):
        st.session_state.pop("dataset_delete_pending", None)
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
                _delete_reviewed_records(pending)
            if cancel.button("Cancel deletion", key="record_manager_cancel"):
                st.session_state.pop("record_manager_pending", None)
                st.rerun()
