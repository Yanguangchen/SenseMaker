"""Review datasets and save reversible analysis exclusions in the record table."""

import hashlib

import pandas as pd
import streamlit as st

from modules.analysis_library import dataset_date_label, dataset_timestamp
from modules.client_report import platform_label
from modules.database import init_db, save_dataset_inclusion
from modules.signals import is_analyzable


def render_dataset_editor(rows: list[dict]) -> tuple[list[dict], bool]:
    records = [row for row in rows if row.get("_id") and is_analyzable(row)]
    if not records:
        st.info("No analyzable records match this view. Choose another dataset or record status.")
        return [], True
    # Row indices from an older view must never be applied to a different record.
    identity = repr((st.session_state.get("analysis_editor_revision", 0), [row["_id"] for row in records]))
    key = "analysis_dataset_editor_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
    st.session_state["analysis_editor_key"] = key
    frame = pd.DataFrame([{
        "Include": not bool(row.get("analysis_excluded")),
        "Record": row.get("title") or " ".join(str(row.get("raw_text") or "").split())[:200] or row["_id"],
        "Dataset": row.get("custom_title") or "Untitled (older records)",
        "Source": platform_label(row), "Status": row.get("status") or "pending",
        "Saved (UTC)": dataset_date_label({"timestamp": dataset_timestamp(row)}),
        "URL": row.get("url", ""),
    } for row in records])
    baseline = st.session_state.get("analysis_editor_frame", {})
    if baseline.get("key") != key or key not in st.session_state:
        baseline = {"key": key, "frame": frame}
        st.session_state["analysis_editor_frame"] = baseline
    # Keep the input table stable across saves: Streamlit applies cell edits to
    # this baseline. Replacing it after each checkbox click can lose the next edit.
    frame = baseline["frame"]
    st.caption("Uncheck Include to exclude a record. Changes save automatically with the shared dataset; "
               "check it again to restore it.")
    edited = st.data_editor(frame, key=key, hide_index=True, width="stretch", num_rows="fixed",
                            disabled=[column for column in frame.columns if column != "Include"],
                            column_config={"Include": st.column_config.CheckboxColumn("Include", width="small", required=True),
                                           "Record": st.column_config.TextColumn("Record", width="large"),
                                           "URL": st.column_config.LinkColumn("Source URL")})
    changes = {str(row["_id"]): bool(edited.iloc[index]["Include"]) for index, row in enumerate(records)
               if bool(edited.iloc[index]["Include"]) != (not bool(row.get("analysis_excluded")))}
    saved = True
    if changes:
        try:
            save_dataset_inclusion(init_db(), records, changes)
        except Exception:
            saved = False
            st.error("Your exclusions could not be saved. Retry saving changes, or refresh datasets to review the latest records.")
            if st.button("Retry saving changes", key="retry_dataset_edits"):
                st.rerun()
        else:
            for row in records:
                if str(row["_id"]) in changes:
                    row["analysis_excluded"] = not changes[str(row["_id"])]
            st.session_state.pop("analysis_last_results", None)
            st.session_state.pop("content_recommendation_plan", None)
            st.session_state.pop("content_records", None)
            st.session_state.pop("latest_report_pdf", None)
            st.session_state.pop("saved_report_pdf", None)
            st.success("Dataset changes saved.")
    included = [row for index, row in enumerate(records) if bool(edited.iloc[index]["Include"])]
    st.caption(f"{len(included)} included · {len(records) - len(included)} excluded · {len(records)} records in this view")
    return included, saved
