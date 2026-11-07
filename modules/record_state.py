"""Remove deleted/stale records from the current session's working copies."""

from collections.abc import MutableMapping
from typing import Any

from modules.database import get_post_id


def rename_record_caches(state: MutableMapping[str, Any], ids: list[str], title: str) -> None:
    """Keep local collection titles aligned so a later save cannot undo a rename."""
    changed = set(ids)

    def ident(row):
        return str(row.get("_id") or get_post_id(str(row.get("url") or "")))

    def update(rows):
        for row in rows:
            if ident(row) in changed:
                row["custom_title"] = title

    for key in ("processed_rows", "firestore_rows", "last_scrape_results", "last_reddit_scrape_results"):
        update(state.get(key) or [])
    for key in ("news_scan", "x_collection", "website_collection", "last_collected_dataset"):
        collection = state.get(key) or {}
        rows = collection.get("rows", [])
        update(rows)
        relevant = [row for row in rows if row.get("url") and not str(row.get("source_type", "")).startswith("batch_")]
        if relevant and all(ident(row) in changed for row in relevant):
            if key == "last_collected_dataset":
                collection["title"] = title
            if collection.get("cloud_save"):
                collection["cloud_save"]["title"] = title
    for collection in (state.get("collection_titles") or {}).values():
        if collection.get("ids") and set(collection["ids"]).issubset(changed):
            collection["title"] = title
    for key in ("record_manager_rows", "analysis_dataset_catalog", "analysis_last_results", "gemini_last_results",
                "content_recommendation_plan", "latest_report_pdf", "saved_report_pdf"):
        state.pop(key, None)


def purge_record_caches(state: MutableMapping[str, Any], result: dict[str, list[str]]) -> None:
    removed = set(result["deleted"] + result["missing"])
    stale = removed | set(result["skipped"])

    def keep(row: dict[str, Any], ids: set[str]) -> bool:
        ident = str(row.get("_id") or get_post_id(str(row.get("url") or "")))
        return ident not in ids

    for key in ("processed_rows", "firestore_rows", "record_manager_rows"):
        if key in state:
            state[key] = [row for row in state[key] if keep(row, stale)]
    if "firestore_rows" in state:
        state["firestore_last_load_count"] = len(state["firestore_rows"])
    for key in ("analysis_last_results", "gemini_last_results"):
        if key in state:
            summary = dict(state[key])
            summary["analyses"] = [row for row in summary.get("analyses", []) if keep(row, stale)]
            summary["processed"] = len(summary["analyses"])
            state[key] = summary
    # Prevent a collected copy from immediately recreating a deleted cloud record.
    for key in ("last_scrape_results", "last_reddit_scrape_results"):
        if state.get(key) is not None:
            state[key] = [row for row in state[key] if keep(row, removed)]
    for key in ("news_scan", "x_collection", "website_collection", "last_collected_dataset"):
        if state.get(key):
            state[key] = {**state[key], "rows": [row for row in state[key].get("rows", []) if keep(row, removed)]}
    for key in ("analysis_dataset_catalog", "content_recommendation_plan", "latest_report_pdf", "saved_report_pdf"):
        state.pop(key, None)
