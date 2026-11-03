"""Save and reuse the Custom website input without triggering collection."""

import logging

import streamlit as st

from modules import database, website_presets as store
from modules.saved_links import library_owner_id
from modules.websites import parse_website_links

log = logging.getLogger(__name__)


def _key(email: str) -> str:
    return f"website_presets_{library_owner_id(email)}"


def _flash(level: str, message: str) -> None:
    st.session_state.website_preset_notice = (level, message)


def _cancel_delete() -> None:
    st.session_state.pop("website_preset_delete_pending", None)


def _refresh(email: str) -> None:
    _cancel_delete()
    try:
        rows = store.list_presets(database.init_db(), email)
        st.session_state[_key(email)] = {"rows": rows, "error": False}
    except Exception:
        log.exception("Website presets could not be loaded")
        st.session_state[_key(email)] = {"rows": [], "error": True}


def _load(row: dict) -> None:
    # Validate before replacing the input, including presets from an older app version.
    try:
        urls = parse_website_links("\n".join(row["urls"]))
    except (TypeError, ValueError) as exc:
        _flash("error", f"This preset could not be loaded: {exc}")
        return
    st.session_state.website_links = "\n".join(urls)
    _cancel_delete()
    _flash("success", f"Loaded {row['name']} · {len(urls)} URLs. Review the list, then click Collect website pages.")


def _save(email: str) -> None:
    try:
        row = store.save_preset(database.init_db(), email,
                                st.session_state.get("website_preset_name", ""),
                                st.session_state.get("website_links", ""))
    except ValueError as exc:
        _flash("error", str(exc))
        return
    except Exception:
        log.exception("Website preset could not be saved")
        _flash("error", "The save could not be confirmed. Your URLs are still here; refresh presets before retrying.")
        return
    _refresh(email)
    st.session_state.website_preset_choice = row["id"]
    st.session_state.website_preset_name = ""
    _flash("success", f"Saved preset: {row['name']} · {len(row['urls'])} URLs.")


def _update(email: str, row: dict) -> None:
    try:
        store.update_preset(database.init_db(), email, row["id"], st.session_state.get("website_links", ""))
    except ValueError as exc:
        _flash("error", str(exc))
        return
    except Exception:
        log.exception("Website preset could not be updated")
        _flash("error", "The update could not be confirmed. Your URLs are still here; refresh presets before retrying.")
        return
    _refresh(email)
    _flash("success", f"Updated preset: {row['name']}.")


def _request_delete(row: dict) -> None:
    st.session_state.website_preset_delete_pending = row


def _delete(email: str, row: dict) -> None:
    try:
        store.delete_preset(database.init_db(), email, row["id"])
    except Exception:
        log.exception("Website preset could not be deleted")
        _flash("error", "The deletion could not be confirmed. Refresh presets before retrying.")
        return
    _refresh(email)
    st.session_state.website_preset_choice = None
    _flash("success", f"Deleted preset: {row['name']}. The URLs in the input and collected records were kept.")


def render_website_presets(email: str) -> None:
    if _key(email) not in st.session_state:
        _refresh(email)
    saved = st.session_state[_key(email)]
    by_id = {row["id"]: row for row in saved["rows"]}
    if st.session_state.get("website_preset_choice") not in by_id:
        st.session_state.website_preset_choice = None
        _cancel_delete()

    with st.container(border=True, key="website_presets"):
        st.markdown("**URL presets**")
        notice = st.session_state.pop("website_preset_notice", None)
        if notice:
            getattr(st, notice[0])(notice[1])
        if saved["error"]:
            st.warning("Your presets could not be loaded. Use Refresh presets to try again.")

        choice_col, refresh_col = st.columns([3, 1], vertical_alignment="bottom")
        choice = choice_col.selectbox(
            "Saved URL preset", [None] + list(by_id), key="website_preset_choice",
            format_func=lambda ident: (f"{by_id[ident]['name']} · {len(by_id[ident]['urls'])} URLs"
                                       if ident in by_id else "Choose a preset"),
            on_change=_cancel_delete, disabled=not by_id,
        )
        refresh_col.button("Refresh presets", key="website_preset_refresh", on_click=_refresh, args=(email,))
        if choice in by_id:
            row = by_id[choice]
            with st.expander("Preview saved URLs"):
                st.code("\n".join(row["urls"]))
            st.caption("Load replaces the URLs above. Update replaces this preset with the URLs above.")
            load, update, delete = st.columns(3)
            load.button("Load preset", key="website_preset_load", on_click=_load, args=(row,), width="stretch")
            update.button("Update preset", key="website_preset_update", on_click=_update,
                          args=(email, row), width="stretch")
            delete.button("Delete preset", key="website_preset_delete", on_click=_request_delete,
                          args=(row,), width="stretch")

            pending = st.session_state.get("website_preset_delete_pending")
            if pending and pending["id"] == choice:
                st.warning(f"Delete preset '{pending['name']}'? This removes the saved list, not collected records.")
                confirm, cancel = st.columns(2)
                confirm.button("Confirm delete preset", key="website_preset_confirm_delete",
                               on_click=_delete, args=(email, pending))
                cancel.button("Cancel", key="website_preset_cancel_delete", on_click=_cancel_delete)

        name, save = st.columns([3, 1], vertical_alignment="bottom")
        name.text_input("New preset name", key="website_preset_name", max_chars=store.MAX_NAME_CHARS,
                        placeholder="For example: Cambodia news sources")
        save.button("Save preset", key="website_preset_save", on_click=_save, args=(email,))
        st.caption("Save preset keeps the URLs currently entered above. It does not collect pages or run analysis.")
