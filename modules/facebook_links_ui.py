"""Inspect and manage the signed-in user's Facebook sources without starting collection."""

import hashlib

import pandas as pd
import streamlit as st

from modules.database import init_db
from modules.saved_links import is_facebook_link, library_owner_id, load_saved_links, remove_saved_links


def forget_removed_facebook_links(user_email: str, urls: list[str]) -> None:
    """Keep the saved library and pending collection in sync after a confirmed removal."""
    owner = library_owner_id(user_email)
    removed = set(urls)
    for scope in (owner, f"{owner}_facebook"):
        library = st.session_state.get(f"saved_links_{scope}")
        if library is not None:
            library["urls"] = [url for url in library["urls"] if url not in removed]
        memo_key = f"links_selection_memo_{scope}"
        if memo_key in st.session_state:
            st.session_state[memo_key] = [url for url in st.session_state[memo_key] if url not in removed]
    if "facebook_wizard_targets" in st.session_state:
        st.session_state["facebook_wizard_targets"] = [
            url for url in st.session_state["facebook_wizard_targets"] if url not in removed]
    # The collector prunes its selection widget against the updated library on
    # the next render. It may already be mounted when deleting from its expander.


def render_facebook_link_list(urls: list[str], *, key_prefix: str, user_email: str | None = None) -> None:
    urls = list(dict.fromkeys(url for url in urls if is_facebook_link(url)))
    notice_key = f"{key_prefix}_delete_notice"
    if notice_key in st.session_state:
        notice = st.session_state.pop(notice_key)
        (st.success if notice["ok"] else st.error)(notice["message"])
    st.caption(f"{len(urls):,} saved Facebook link{'s' if len(urls) != 1 else ''} in your account.")
    if not urls:
        st.info("No Facebook links are saved yet. Add group, page or post URLs in Your links when setting up a collection.")
        return
    query = st.text_input("Find a saved Facebook link", key=f"{key_prefix}_search",
                          placeholder="Search any part of a saved URL")
    shown = [url for url in urls if query.strip().casefold() in url.casefold()]
    st.caption(f"Showing {len(shown):,} of {len(urls):,} links. Open a URL to inspect it in Facebook.")
    if shown:
        table_options = dict(hide_index=True, width="stretch", height=min(420, 38 + 35 * len(shown)),
                             column_config={"Facebook URL": st.column_config.LinkColumn("Facebook URL", width="large")})
        if user_email:
            st.caption("Select links to delete from your saved library and future collections. Collected posts and analyses are kept.")
            # Edits use row indices. A changed filter/library must get a new key
            # so an old selection can never point at a different link.
            identity = hashlib.sha256("\n".join(shown).encode()).hexdigest()[:16]
            editor_key = f"{key_prefix}_delete_table_{identity}"
            st.session_state[f"{key_prefix}_delete_editor_key"] = editor_key
            table_options["column_config"]["Delete"] = st.column_config.CheckboxColumn("Delete", width="small", required=True)
            edited = st.data_editor(pd.DataFrame({"Delete": False, "Facebook URL": shown}),
                                    key=editor_key, num_rows="fixed", disabled=["Facebook URL"], **table_options)
            selected = [url for index, url in enumerate(shown) if bool(edited.iloc[index]["Delete"])]
            if st.button(f"Delete {len(selected)} selected link{'s' if len(selected) != 1 else ''}",
                         key=f"{key_prefix}_delete", disabled=not selected, type="primary") and selected:
                # This runs after the dialog's access check, never as a widget callback.
                try:
                    remove_saved_links(init_db(), user_email, selected)
                except Exception:
                    # A large delete can succeed in part. Reconcile confirmed removals
                    # when possible; otherwise require a refresh before further deletes.
                    library_key = f"saved_links_{library_owner_id(user_email)}_facebook"
                    try:
                        fresh = [url for url in load_saved_links(init_db(), user_email) if is_facebook_link(url)]
                        forget_removed_facebook_links(user_email, [url for url in urls if url not in fresh])
                        st.session_state[library_key] = {"urls": fresh, "error": False}
                    except Exception:
                        st.session_state[library_key]["error"] = True
                    st.session_state[notice_key] = {"ok": False, "message":
                        "Some links could not be deleted. Refresh your saved links and retry any that remain."}
                else:
                    forget_removed_facebook_links(user_email, selected)
                    st.session_state[notice_key] = {"ok": True, "message":
                        f"Deleted {len(selected)} saved link{'s' if len(selected) != 1 else ''}. Collected posts and analyses are kept."}
                st.rerun()
        else:
            st.dataframe(pd.DataFrame({"Facebook URL": shown}), **table_options)
    else:
        st.info("No saved Facebook links match this search.")
    st.download_button("Download all saved Facebook links", "\n".join(urls) + "\n",
                       file_name="saved-facebook-links.txt", mime="text/plain",
                       key=f"{key_prefix}_download", on_click="ignore")


def render_saved_facebook_links(user_email: str) -> None:
    owner = library_owner_id(user_email)
    # Share the collector's account-scoped cache so additions/removals are reflected.
    library_key = f"saved_links_{owner}_facebook"

    def refresh() -> None:
        try:
            with st.spinner("Loading saved Facebook links…"):
                urls = [url for url in load_saved_links(init_db(), user_email) if is_facebook_link(url)]
            st.session_state[library_key] = {"urls": urls, "error": False}
        except Exception:
            previous = st.session_state.get(library_key, {})
            st.session_state[library_key] = {"urls": previous.get("urls", []), "error": True}

    # Run refresh after the enclosing dialog's access check, not in a callback.
    requested_refresh = st.button("Refresh saved Facebook links", key=f"facebook_link_inspector_{owner}_refresh")
    if library_key not in st.session_state or requested_refresh:
        refresh()
    library = st.session_state[library_key]
    if library["error"]:
        st.warning("Saved Facebook links could not be refreshed. Any links shown are from the last successful load.")
        if not library["urls"]:
            return
    render_facebook_link_list(library["urls"], key_prefix=f"facebook_link_inspector_{owner}",
                             user_email=user_email if not library["error"] else None)
