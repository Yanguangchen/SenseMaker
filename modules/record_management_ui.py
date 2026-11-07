"""Browse and delete reviewed selections from the shared source-record library."""

from datetime import datetime
from math import ceil

import pandas as pd
import streamlit as st

from modules.analysis_library import dataset_date_label, dataset_key, dataset_options, select_records
from modules.client_report import platform_label
from modules.config import get_setting
from modules.database import (
    delete_dataset_records, delete_datasets_records, delete_records, init_db, load_record_catalog, upsert_post,
    rename_dataset,
)
from modules.dataset_cards import apply_dataset_card_theme, featured_datasets, render_dataset_cards, viewer_timezone
from modules.record_state import purge_record_caches, rename_record_caches
from modules.signals import NON_CONTENT_TYPES

DATASET_SELECTION = "record_manager_datasets_to_delete"
DATASET_REVIEW = "datasets_delete_pending"


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
    for key in ("record_manager_pending", "record_manager_row_pending", "dataset_delete_pending", DATASET_REVIEW):
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
    deleting = st.session_state.get("record_manager_dataset_delete_mode", False)
    render_dataset_cards(groups, section, local_tz, key_prefix="record_manager",
                         on_select=_toggle_dataset_selection if deleting else _open_dataset,
                         selected=st.session_state.get(DATASET_SELECTION, []) if deleting else None)


def _clear_dataset_selection() -> None:
    st.session_state[DATASET_SELECTION] = []
    _clear_deletion_reviews()


def _toggle_dataset_selection(dataset: str) -> None:
    selected = list(st.session_state.get(DATASET_SELECTION, []))
    if dataset in selected:
        selected.remove(dataset)
    else:
        selected.append(dataset)
    st.session_state[DATASET_SELECTION] = selected
    _clear_deletion_reviews()


def _delete_reviewed_datasets(datasets: dict[str, dict[str, str]]) -> None:
    ids = [ident for versions in datasets.values() for ident in versions]
    progress = st.progress(0, text="Deleting selected datasets…")
    try:
        db = init_db()
        result = delete_datasets_records(db, datasets, on_progress=lambda done, total:
            progress.progress(done / total, text=f"Checked {done} of {total} reviewed records"))
    except Exception:
        result = {"deleted": [], "skipped": [], "missing": [], "unconfirmed": ids}
    purge_record_caches(st.session_state, {**result, "skipped": result["skipped"] + result["unconfirmed"]})
    st.session_state.pop("record_manager_rows", None)
    st.session_state["record_manager_needs_refresh"] = True
    removed = []
    if not result["unconfirmed"]:
        try:
            fresh = load_record_catalog(db)
            st.session_state["record_manager_rows"] = fresh
            st.session_state.pop("record_manager_needs_refresh", None)
            remaining = {dataset_key(row) for row in fresh}
            removed = [key for key in datasets if key not in remaining]
        except Exception:
            pass  # Confirmed deletion counts remain useful if refreshing fails.
    st.session_state["datasets_delete_notice"] = {
        "result": result, "selected_count": len(datasets), "removed_count": len(removed),
    }
    _clear_dataset_selection()
    st.rerun()


def _render_datasets_deletion(rows: list[dict], groups: list[dict]) -> None:
    by_key = {group["key"]: group for group in groups}
    selected = [key for key in st.session_state.get(DATASET_SELECTION, []) if key in by_key]
    st.session_state[DATASET_SELECTION] = selected
    datasets = {key: {} for key in selected}
    for row in rows:
        key = dataset_key(row)
        if key in datasets:
            datasets[key][row["_id"]] = row["_version"]
    review = {"collection": st.session_state.get("record_manager_collection"), "datasets": datasets}
    if st.session_state.get(DATASET_REVIEW) != review:
        st.session_state.pop(DATASET_REVIEW, None)
    count = sum(len(versions) for versions in datasets.values())
    st.caption("Select dataset cards below. Selections carry across both tabs and search pages; "
               "deletion includes every record in each selected dataset.")
    if selected:
        st.dataframe(pd.DataFrame([{"Dataset": by_key[key]["title"], "Records": len(datasets[key])}
                                   for key in selected]), hide_index=True, width="stretch")
    st.caption(f"{len(selected)} datasets selected · {count:,} records")
    review_col, clear = st.columns(2)
    if review_col.button(f"Review deletion of {len(selected)} dataset(s)", key="record_manager_delete_datasets_review",
                         disabled=not count):
        _clear_selection()
        st.session_state[DATASET_REVIEW] = review
    clear.button("Clear dataset selection", key="record_manager_datasets_clear",
                 on_click=_clear_dataset_selection, disabled=not selected)
    if st.session_state.get(DATASET_REVIEW) == review and count:
        st.warning(f"Permanently delete these {len(selected)} datasets and all {count:,} reviewed records "
                   "and their saved analyses? This affects all users and cannot be undone. "
                   "Changed, moved and newly added records are kept.")
        with st.expander("Review all selected records"):
            st.dataframe(_table([row for row in rows if dataset_key(row) in datasets]),
                         hide_index=True, width="stretch")
        confirm, cancel = st.columns(2)
        if confirm.button(f"Permanently delete {len(selected)} dataset(s)",
                          key="record_manager_delete_datasets_confirm", type="primary"):
            _delete_reviewed_datasets(datasets)
        cancel.button("Cancel dataset deletion", key="record_manager_datasets_cancel", on_click=_clear_deletion_reviews)


def _render_dataset_browser(rows: list[dict], groups: list[dict]) -> None:
    local_tz = viewer_timezone()
    apply_dataset_card_theme()
    if st.toggle("Select datasets to delete", key="record_manager_dataset_delete_mode", on_change=_clear_dataset_selection):
        _render_datasets_deletion(rows, groups)
    is_today, featured = featured_datasets(groups, datetime.now(local_tz))
    latest_tab, find_tab = st.tabs(["Today's datasets" if is_today else "Latest datasets", "Find a dataset"])
    with latest_tab:
        if is_today:
            st.caption(f"{len(featured)} datasets saved today · Newest first · Times in {local_tz}")
        else:
            st.caption(f"No datasets saved today. Showing the {len(featured)} latest datasets · Times in {local_tz}")
        _render_dataset_cards(featured, "featured", local_tz)
    with find_tab:
        _render_dataset_search(rows, groups, local_tz)


def _set_dataset_page(page: int) -> None:
    st.session_state["record_manager_dataset_page"] = page


def _render_dataset_pagination(page: int, total: int) -> None:
    if total <= 1:
        return
    start = max(1, min(page - 2, total - 4))
    numbers = list(range(1, total + 1)) if total <= 7 else sorted({1, total, *range(start, start + 5)})
    with st.container(horizontal=True, horizontal_alignment="center", vertical_alignment="center",
                      key="record_manager_dataset_pagination"):
        st.button("Previous", key="record_manager_dataset_previous", disabled=page == 1,
                  on_click=_set_dataset_page, args=(page - 1,))
        previous = 0
        for number in numbers:
            if number > previous + 1:
                st.markdown("…", width="content")
            st.button(str(number), key=f"record_manager_dataset_page_{number}",
                      type="primary" if number == page else "secondary",
                      help=f"Current page: {number}" if number == page else f"Go to page {number}",
                      on_click=_set_dataset_page, args=(number,))
            previous = number
        st.button("Next", key="record_manager_dataset_next", disabled=page == total,
                  on_click=_set_dataset_page, args=(page + 1,))


def _render_dataset_search(rows: list[dict], groups: list[dict], local_tz) -> None:
    with st.expander("Show / hide filters", expanded=False):
        search = st.text_input("Search datasets", key="record_manager_dataset_search", max_chars=200,
                               placeholder="Search by dataset name, page title, URL or record ID")
    query = search.strip()
    if query:
        keys = {dataset_key(row) for row in filter_records(rows, None, None, None, search)}
        matches = [group for group in groups if group["key"] in keys]
    else:
        matches = groups
    if st.session_state.get("record_manager_dataset_query") != query:
        st.session_state["record_manager_dataset_page"] = 1
        st.session_state["record_manager_dataset_query"] = query
    scope = "matching datasets" if query else "datasets"
    st.caption(f"{len(matches)} {scope} · {sum(group['count'] for group in matches):,} records · Newest first")
    if not matches:
        st.info("No datasets match your search. Open Show / hide filters to change or clear your search.")
    else:
        pages = list(range(1, ceil(len(matches) / 12) + 1))
        if st.session_state.get("record_manager_dataset_page") not in pages:
            st.session_state["record_manager_dataset_page"] = 1
        page = st.session_state["record_manager_dataset_page"]
        _render_dataset_cards(matches[(page - 1) * 12:page * 12], "search", local_tz)
        _render_dataset_pagination(page, len(pages))
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


def _render_dataset_rename(rows: list[dict], dataset: str | None) -> None:
    if dataset is None:
        return
    records = [row for row in rows if dataset_key(row) == dataset]
    if not records:
        return
    with st.expander("Rename dataset"):
        st.caption(f"Rename all {len(records)} records in this dataset. Source text, collection dates and saved analyses stay intact. "
                   "Using an existing title groups these records with that dataset.")
        current_title = str(records[0].get("custom_title") or "").strip()
        with st.form(f"record_manager_rename_{dataset}"):
            title = st.text_input("Dataset title", value=current_title, max_chars=160,
                                  key=f"record_manager_title_{dataset}")
            submitted = st.form_submit_button("Save dataset title")
        if submitted:
            try:
                result = rename_dataset(init_db(), dataset, {row["_id"]: row["_version"] for row in records}, title)
            except ValueError as exc:
                st.error(str(exc))
                return
            except Exception:
                st.error("The dataset title could not be saved. Refresh datasets and try again.")
                return
            title = title.strip()
            rename_record_caches(st.session_state, result["renamed"], title)
            _clear_selection()
            _clear_dataset_selection()
            st.session_state.pop("record_manager_needs_refresh", None)
            complete = not any(result[key] for key in ("skipped", "missing", "unconfirmed"))
            st.session_state["record_manager_rename_notice"] = {"title": title, "result": result, "complete": complete}
            if complete:
                st.session_state["record_manager_renamed_dataset"] = f"title:{title}"
            st.rerun()


def render_record_management() -> None:
    renamed = st.session_state.pop("record_manager_renamed_dataset", None)
    if renamed:
        st.session_state["record_manager_dataset"] = renamed
        st.session_state["record_manager_details"] = True
    title, refresh_col = st.columns([12, 1], vertical_alignment="center")
    title.subheader("Clean & manage data")
    rename_notice = st.session_state.pop("record_manager_rename_notice", None)
    if rename_notice:
        result = rename_notice["result"]
        if rename_notice["complete"]:
            st.success(f'Dataset renamed to "{rename_notice["title"]}".')
        else:
            st.warning(f"Renamed {len(result['renamed'])} records. "
                       f"{len(result['skipped'])} changed, {len(result['missing'])} were removed, "
                       f"and {len(result['unconfirmed'])} could not be confirmed. Refresh and review the remaining records before retrying.")
    # Preserve the search when its widget is temporarily hidden by dataset details.
    if "record_manager_dataset_search" in st.session_state:
        st.session_state["record_manager_dataset_search"] = st.session_state["record_manager_dataset_search"]
    collection = get_setting("FIREBASE_COLLECTION", "raw_posts") or "raw_posts"
    if st.session_state.get("record_manager_collection") != collection:
        st.session_state.pop("record_manager_rows", None)
        st.session_state.pop("record_manager_error", None)
        st.session_state.pop("record_manager_needs_refresh", None)
        _clear_selection()
        _clear_dataset_selection()
        st.session_state["record_manager_collection"] = collection
    recent = st.session_state.get("last_collected_dataset")
    if isinstance(recent, dict) and recent.get("rows") and st.session_state.get("record_manager_last_collection_id") != recent.get("id"):
        st.session_state.pop("record_manager_rows", None)
        st.session_state["record_manager_last_collection_id"] = recent.get("id")
    refresh = refresh_col.button("", icon=":material/refresh:", key="record_manager_refresh", help="Refresh datasets")
    if refresh or ("record_manager_rows" not in st.session_state
                   and not st.session_state.get("record_manager_needs_refresh")
                   and not st.session_state.get("record_manager_error")):
        _clear_selection()
        st.session_state.pop("record_manager_error", None)
        try:
            with st.spinner("Loading saved records…"):
                db = init_db()
                if isinstance(recent, dict) and recent.get("rows") and not recent.get("saved"):
                    save_timestamp = datetime.now().astimezone().isoformat()
                    title = str(recent.get("title") or "").strip()
                    for post in recent["rows"]:
                        url = str(post.get("url") or "").strip()
                        if url and post.get("source_type") not in NON_CONTENT_TYPES:
                            payload = {**post, "custom_title": title, "saved_at": save_timestamp}
                            if not payload.get("scraped_at"):
                                payload["scraped_at"] = save_timestamp
                            upsert_post(db, payload)
                    recent["saved"] = True
                    st.session_state.pop("analysis_dataset_catalog", None)
                st.session_state["record_manager_rows"] = load_record_catalog(db)
            st.session_state.pop("record_manager_needs_refresh", None)
        except Exception:
            st.session_state.pop("record_manager_rows", None)
            st.session_state["record_manager_error"] = "Datasets could not be loaded. Check the connection and click the refresh icon to try again."
    notice = st.session_state.pop("record_manager_notice", None)
    if notice:
        st.success(f"Deleted {len(notice['deleted'])} record(s).")
        if notice["skipped"]:
            st.warning(f"Kept {len(notice['skipped'])} record(s) changed since loading. Refresh to review their current versions.")
        if notice["missing"]:
            st.info(f"{len(notice['missing'])} record(s) had already been deleted.")
    if st.session_state.get("record_manager_error"):
        st.error(st.session_state["record_manager_error"])
    bulk_notice = st.session_state.pop("datasets_delete_notice", None)
    if bulk_notice:
        result = bulk_notice["result"]
        if bulk_notice["removed_count"] == bulk_notice["selected_count"]:
            st.success(f"Deleted {bulk_notice['removed_count']} dataset(s) and {len(result['deleted'])} record(s).")
        else:
            st.warning(f"Deleted {len(result['deleted'])} record(s). "
                       f"{bulk_notice['removed_count']} of {bulk_notice['selected_count']} datasets confirmed removed. "
                       "Dataset removal is incomplete or could not be verified. Refresh datasets before retrying.")
        if result["skipped"]:
            st.info(f"Kept {len(result['skipped'])} record(s) changed since loading or moved to another dataset.")
        if result["missing"]:
            st.info(f"{len(result['missing'])} record(s) had already been deleted.")
        if result["unconfirmed"]:
            st.error(f"Could not confirm deletion of {len(result['unconfirmed'])} remaining record(s). "
                     "Earlier confirmed deletions are included in the count above.")
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
        st.caption("Click the refresh icon to reload the library.")
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
    _render_dataset_rename(rows, dataset)
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
