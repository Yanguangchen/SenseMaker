"""Landscape paper cards that open their velocity tables in a notepad dialog."""

import logging
import os
from html import escape
from pathlib import Path

import streamlit as st

from modules import database, velocity_jobs as jobs, velocity_tracking as store
from modules.client_report import CSS_PATH, format_time
from modules.saved_links import library_owner_id
from modules.ui import show_running_glow
from modules.velocity_table import velocity_card_sheet

log = logging.getLogger(__name__)
PAPER_CSS = Path(__file__).parent / "theme" / "velocity-notepad.css"
MODAL_KEY = "velocity_open_notepad"


def _close_card() -> None:
    st.session_state.pop(MODAL_KEY, None)


def _open_card(email: str, card_id: str) -> None:
    st.session_state[MODAL_KEY] = {"owner": library_owner_id(email), "card_id": card_id}
    # Streamlit supports one dialog at a time; source collectors stay mounted.
    st.session_state.pop("focus_workspace_modal", None)
    st.session_state["x_focus_open"] = False


def _card_name(card: dict, index: int) -> str:
    name = card.get("name", "Velocity tracking card")
    return f"Velocity tracking card {index}" if name == "Velocity tracking card" else name


def _show_notice(key: str) -> None:
    notice = st.session_state.pop(f"{key}_notice", None)
    if notice:
        st.success(notice)


def _update_owned_cards(state: dict, cards: list[dict]) -> None:
    state["cards"] = [*cards, *(card for card in state["cards"] if card.get("shared"))]


def _render_cover(email: str, card: dict, index: int, key: str) -> None:
    posts, runs = store.card_posts(card), card.get("runs", [])
    name = _card_name(card, index)
    eyebrow = "SHARED VELOCITY" if card.get("shared") else "VELOCITY"
    job = jobs.latest_job(email)
    running = job and job.card_id == card["id"] and job.status in {"running", "saving"}
    detail = "Checking views…" if running else (f"Last run · {format_time(runs[-1]['at'])}" if runs else "Ready for your first check")
    checks = "".join(f'<span class="sm-note-check{" is-filled" if i < len(runs) else ""}">{i + 1}</span>'
                     for i in range(store.SHOWN_RUNS))
    with st.container(key=f"sm_velocity_cover_{card['id']}"):
        st.html(
            '<div class="sm-note-cover">'
            f'<div class="sm-note-eyebrow">{eyebrow} <span>NO. {index:02}</span></div>'
            f'<h3>{escape(name)}</h3>'
            f'<p class="sm-note-meta">{len(posts)} / 10 posts <span>·</span> {card.get("run_count", len(runs))} saved checks</p>'
            f'<div class="sm-note-checks" aria-label="Latest eight checks">{checks}</div>'
            f'<div class="sm-note-foot"><span>{escape(detail)}</span><b>Open notepad ↗</b></div></div>'
        )
        # A native button covers the paper, so its whole surface supports click,
        # keyboard focus and Enter/Space without custom JavaScript.
        st.button(f"Open {name}", key=f"{key}_open_{card['id']}", width="stretch",
                  on_click=_open_card, args=(email, card["id"]))


@st.dialog("Velocity notepad", width="large", on_dismiss=_close_card)
def _card_modal(email: str, key: str, *, hosted: bool) -> None:
    # Dialog widgets rerun independently of the dashboard's authorization gate.
    from modules.auth import allowed_emails, verify_access
    try:
        if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != email:
            raise PermissionError()
    except Exception:
        _close_card()
        st.rerun()
    selection = st.session_state.get(MODAL_KEY, {})
    state = st.session_state[key]
    card = next((row for row in state["cards"] if row["id"] == selection.get("card_id")), None)
    if card is None or selection.get("owner") != library_owner_id(email):
        _close_card()
        st.rerun()
    with st.container(key="sm_velocity_modal"):
        _show_notice(key)
        if state["error"]:
            st.warning("Saved cards could not be refreshed. Close this notepad and refresh cards & checks to try again.")
        _render_card(email, card, state["cards"].index(card) + 1, state, key, hosted=hosted)
        st.button("Close notepad", key=f"{key}_close", on_click=_close_card)
        if MODAL_KEY not in st.session_state:
            st.rerun()


@st.fragment(run_every=2)
def _run_progress(email: str, job_id: str) -> None:
    from modules.auth import allowed_emails, verify_access
    try:
        if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != email:
            st.rerun(scope="app")
            return
    except Exception:
        st.rerun(scope="app")
        return
    job = jobs.latest_job(email)
    if job is None or job.id != job_id or job.status not in {"running", "saving"}:
        st.rerun(scope="app")
        return
    show_running_glow()
    st.progress(job.completed / len(job.posts), text=job.message)


def _render_card_history(card: dict) -> None:
    st.html(velocity_card_sheet(card))
    if not card.get("runs"):
        return
    latest = {row["post_id"]: row for row in card["runs"][-1].get("results", [])}
    missing = [(index, post, latest[post["id"]]) for index, post in enumerate(store.card_posts(card), 1)
               if post["id"] in latest and latest[post["id"]].get("views") is None]
    if missing:
        with st.expander("Why counts are unavailable", expanded=True):
            for index, post, result in missing:
                st.write(f"Post {index}: {result.get('reason') or 'No view count was found on this post.'}")
                st.link_button(f"Open post {index} on Facebook", post["url"])
            st.caption("A view-based status and percentage change need two consecutive view counts. "
                       "If Facebook does not display views for a photo or text post, this card cannot measure its views.")


def _render_remarks(email: str, card: dict, state: dict, key: str, card_key: str) -> None:
    posts = store.card_posts(card)
    if not posts:
        return
    with st.expander("Edit remarks"):
        by_id = {post["id"]: post for post in posts}
        post_id = st.selectbox("Post for remark", list(by_id), key=f"{card_key}_remark_post",
                               format_func=lambda ident: by_id[ident]["url"])
        with st.form(f"{card_key}_remark_form_{post_id}"):
            remark = st.text_area("Remark", value=by_id[post_id].get("remark", ""),
                                  key=f"{card_key}_remark_{post_id}", height=100,
                                  max_chars=store.MAX_REMARK_CHARS,
                                  placeholder="Add a note about this post…")
            save = st.form_submit_button("Save remark", disabled=state["error"])
        if save:
            try:
                _update_owned_cards(state, store.save_remark(database.init_db(), email, card["id"], post_id, remark))
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                log.exception("Velocity remark could not be saved")
                st.error("The remark could not be saved. Your text is still here; try Save remark again.")
            else:
                st.session_state[f"{key}_notice"] = "Remark saved."
                st.rerun()


def _render_card(email: str, card: dict, index: int, state: dict, key: str, *, hosted: bool) -> None:
    posts = store.card_posts(card)
    card_key = f"{key}_{card['id']}"
    job = jobs.latest_job(email)
    # A worker can finish after the workspace loaded its cards but before this
    # card renders. Refresh once even if no polling fragment was mounted yet.
    if job and job.status == "complete" and st.session_state.get(f"{key}_seen_run") != job.id and not state["error"]:
        st.rerun()
    busy = bool(job and job.status in jobs.BUSY)
    card_busy = busy and job.card_id == card["id"]
    with st.container(key=f"velocity_card_{card['id']}"):
        if card.get("shared"):
            st.subheader(_card_name(card, index))
            st.caption(f"{len(posts)} / {store.MAX_POSTS_PER_CARD} posts · Shared by another approved user · View only")
            _render_card_history(card)
            st.caption("The creator runs and edits this card. Use Refresh cards & checks to see their latest saved results.")
            return
        title, actions = st.columns([4, 1], vertical_alignment="center")
        title.subheader(_card_name(card, index))
        title.caption(f"{len(posts)} / {store.MAX_POSTS_PER_CARD} posts · Facebook views")
        if actions.button("Remove card", key=f"{key}_remove_{card['id']}", disabled=state["error"] or card_busy,
                          help="The collected posts and their saved checks are kept."):
            try:
                _update_owned_cards(state, store.remove_card(database.init_db(), email, card["id"]))
            except Exception:
                log.exception("Velocity card could not be removed")
                st.error("The card could not be removed. Refresh cards & checks before trying again.")
            else:
                st.session_state[f"{key}_notice"] = "Card removed. Collected posts and checks are kept."
                _close_card()
                st.rerun()

        with st.expander("Rename card"):
            with st.form(f"{card_key}_rename"):
                name = st.text_input("Card name", value=_card_name(card, index), key=f"{card_key}_name",
                                     max_chars=store.MAX_NAME_CHARS)
                rename = st.form_submit_button("Save name", disabled=state["error"])
            if rename:
                try:
                    _update_owned_cards(state, store.rename_card(database.init_db(), email, card["id"], name))
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    log.exception("Velocity card could not be renamed")
                    st.error("The card could not be renamed. Refresh cards & checks before trying again.")
                else:
                    st.session_state[f"{key}_notice"] = "Card renamed."
                    st.rerun()

        if st.button("Run", key=f"{card_key}_run", type="primary", disabled=state["error"] or not posts or busy,
                     help="Check the displayed views for every saved post in this card and save a new check."):
            try:
                jobs.start_run(email, card["id"], hosted=hosted)
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                log.exception("Velocity run could not start")
                st.error("The run could not start. Refresh cards & checks and try again.")
            else:
                st.rerun()
        if job and job.card_id == card["id"]:
            if job.status in {"running", "saving"}:
                _run_progress(email, job.id)
            elif job.status in {"error", "save_failed"}:
                st.error(job.error)
                if job.status == "save_failed" and st.button("Retry saving check", key=f"{card_key}_retry_save"):
                    jobs.retry_save(email, job.id)
                    st.rerun()
            elif job.status == "complete" and job.run.get("warning"):
                st.warning(job.run["warning"])
        _render_card_history(card)
        _render_remarks(email, card, state, key, card_key)
        if posts and not card.get("runs"):
            st.caption("Click Run to save the first view-count check. Your Facebook connection from Scrape Data is used.")

        if len(posts) < store.MAX_POSTS_PER_CARD:
            generation = st.session_state.get(f"{card_key}_form_generation", 0)
            with st.form(f"{card_key}_add_post_{generation}"):
                url = st.text_input("Provoker's post", placeholder="Add a Facebook post URL when ready",
                                    key=f"{card_key}_url_{generation}")
                submitted = st.form_submit_button("Add post", disabled=state["error"] or card_busy)
            if submitted:
                try:
                    _update_owned_cards(state, store.add_post(database.init_db(), email, card["id"], url))
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    log.exception("Velocity post could not be saved")
                    st.error("The post could not be saved. Refresh cards & checks before trying again.")
                else:
                    st.session_state[f"{card_key}_form_generation"] = generation + 1
                    st.session_state[f"{key}_notice"] = "Facebook post added to the card."
                    st.rerun()
        else:
            st.caption("All 10 post rows are in use. Remove a post to add another.")

        if posts:
            with st.expander("Manage posts"):
                by_id = {post["id"]: post for post in posts}
                choice = st.selectbox("Post to remove", list(by_id), key=f"{card_key}_post_to_remove",
                                      format_func=lambda ident: by_id[ident]["url"])
                if st.button("Remove post", key=f"{card_key}_remove_post", disabled=state["error"] or card_busy):
                    try:
                        _update_owned_cards(state, store.remove_post(database.init_db(), email, card["id"], choice))
                    except Exception:
                        log.exception("Velocity post could not be removed")
                        st.error("The post could not be removed. Refresh cards & checks before trying again.")
                    else:
                        st.session_state[f"{key}_notice"] = "Post removed from the card. Its collected record and checks are kept."
                        st.rerun()


def render_velocity_workspace(email: str, *, hosted: bool | None = None) -> None:
    from modules.auth import allowed_emails

    if hosted is None:
        hosted = os.path.exists("/mount/src") or os.getenv("SENSEMAKER_HOSTED") == "1"
    key = f"velocity_tracking_{library_owner_id(email)}"
    st.html(CSS_PATH)
    st.html(PAPER_CSS)
    st.subheader("Velocity Tracking")
    st.caption("Cards and saved results are shared with approved users. Run and edit your own cards.")
    toolbar = st.container(horizontal=True, vertical_alignment="center", key="sm_velocity_toolbar")
    create_slot = toolbar.empty()
    refresh = toolbar.button("Refresh cards & checks", key=f"{key}_refresh")
    count_slot = toolbar.empty()
    job = jobs.latest_job(email)
    finished = bool(job and job.status == "complete" and st.session_state.get(f"{key}_seen_run") != job.id)
    owners = sorted(library_owner_id(owner) for owner in allowed_emails())
    if key not in st.session_state or refresh or finished or st.session_state[key].get("shared_owners") != owners:
        state = st.session_state.get(key, {"cards": []})
        state["cards"] = [card for card in state["cards"] if not card.get("shared") or card.get("owner_id") in owners]
        try:
            state["cards"] = store.load_workspace_cards(database.init_db(), email)
            state["shared_owners"] = owners
            state["error"] = False
            if finished:
                st.session_state[f"{key}_seen_run"] = job.id
                found = sum(row.get("views") is not None for row in job.run["results"])
                st.session_state[f"{key}_notice"] = f"Check saved. View counts available for {found} of {len(job.posts)} posts."
        except Exception:
            log.exception("Velocity cards could not be loaded")
            state["error"] = True
        st.session_state[key] = state
    state = st.session_state[key]
    cards = state["cards"]
    own_count = sum(not card.get("shared") for card in cards)

    selection = st.session_state.get(MODAL_KEY, {})
    if selection and (selection.get("owner") != library_owner_id(email)
                      or st.session_state.get("focus_workspace_modal") or st.session_state.get("x_focus_open")
                      or not any(row["id"] == selection.get("card_id") for row in cards)):
        _close_card()
    if MODAL_KEY not in st.session_state:
        _show_notice(key)
    if state["error"]:
        st.warning("Your velocity cards could not be loaded. Refresh cards & checks to try again. Any cards shown are from the last successful load.")
    count_slot.caption(f"{len(cards)} cards · {own_count} / {store.MAX_CARDS} created by you")
    if create_slot.button("Create velocity tracking card", key=f"{key}_create", type="primary",
                          disabled=state["error"] or own_count >= store.MAX_CARDS):
        try:
            _update_owned_cards(state, store.add_card(database.init_db(), email))
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            log.exception("Velocity card could not be saved")
            st.error("The card could not be saved. Refresh cards & checks before trying again.")
        else:
            st.session_state[f"{key}_notice"] = "Velocity tracking card created. Add posts whenever you're ready."
            st.rerun()
    if own_count >= store.MAX_CARDS:
        st.info("You have created all 10 cards. Remove one of your cards to create another.")
    if not cards:
        if not state["error"]:
            st.info("No velocity cards yet. Create your first notepad, then open it to add posts.")
        return

    with st.container(key="sm_velocity_grid"):
        for index, card in enumerate(cards, 1):
            _render_cover(email, card, index, key)
    if MODAL_KEY in st.session_state:
        _card_modal(email, key, hosted=hosted)
    elif job and job.status in {"running", "saving"}:
        # Closing the notepad does not stop the worker or its completion refresh.
        _run_progress(email, job.id)
