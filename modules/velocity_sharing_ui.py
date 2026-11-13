"""Share controls and the isolated read-only card page."""

import logging
from pathlib import Path

import streamlit as st

from modules import database, velocity_sharing as sharing
from modules.client_report import CSS_PATH
from modules.velocity_table import velocity_card_sheet
from modules.velocity_analysis_table import analysis_table

log = logging.getLogger(__name__)


def render_share_controls(email: str, card: dict, *, disabled: bool = False) -> list[dict] | None:
    if card.get("shared"):
        return None
    st.caption("Anyone with this link can view this card’s saved results, post links, analysis and remarks. "
               "They cannot edit the card or run checks. New saved results appear when they refresh.")
    token = card.get("share_token")
    if token:
        try:
            url = sharing.share_url(st.context.url, token)
            st.code(url, language=None)
            st.caption("Use the copy button above to copy the share link.")
            st.link_button("Open shared card", url)
        except ValueError as exc:
            st.info(str(exc))
    label = "Revoke share link" if token else "Create share link"
    if st.button(label, key=f"velocity_share_{card['id']}", disabled=disabled):
        try:
            return sharing.set_sharing(database.init_db(), email, card["id"], not bool(token))
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        except Exception:
            log.exception("Velocity sharing could not be updated")
            st.error("Sharing could not be updated. Try again.")
    return None


def render_shared_card_page(token: str) -> None:
    st.html(CSS_PATH)
    st.html(Path(__file__).parent / "theme" / "velocity-notepad.css")
    st.caption("CrisisRadar · Shared velocity card · Read-only")
    st.button("Refresh results")  # Each interaction reloads and revalidates the link.
    try:
        card = sharing.load_shared_card(database.init_db(), token)
    except Exception:
        log.exception("Shared velocity card could not be loaded")
        st.error("This card could not be loaded. Try refreshing in a moment.")
        return
    if card is None:
        st.warning("This share link is invalid or no longer available.")
        return
    st.subheader(card["name"])
    st.caption("Saved results only · Likes includes all reactions · Missing counts are unavailable, not zero.")
    velocity, analysis = st.tabs(["Velocity", "Analysis"])
    with velocity:
        st.html(velocity_card_sheet(card))
        if not card["runs"]:
            st.info("No checks have been saved for this card yet.")
    with analysis:
        st.html(analysis_table({**card, "runs": [{"results": card["analysis_results"]}]}))
        st.caption("Sentiment summarizes sampled comments, not all comments on Facebook.")
