"""Landscape paper cards that open their velocity tables in a notepad dialog."""

import logging
import os
from html import escape
from pathlib import Path

import streamlit as st

from modules import database, velocity_jobs as jobs, velocity_schedule as schedule, velocity_tracking as store
from modules.client_report import CSS_PATH, format_time
from modules.saved_links import library_owner_id
from modules.ui import show_running_glow
from modules.velocity_table import velocity_card_sheet
from modules.velocity_metrics import METRIC, engagement_count
from modules.velocity_analysis_table import analysis_table
from modules.config import get_ai_provider, get_ai_model

log = logging.getLogger(__name__)
PAPER_CSS = Path(__file__).parent / "theme" / "velocity-notepad.css"
MODAL_KEY = "velocity_open_notepad"
REMARK_MODAL_KEY = "velocity_open_remarks"
ANALYSIS_TAB_KEY = "velocity_show_analysis"


def _close_card() -> None:
    st.session_state.pop(MODAL_KEY, None)
    st.session_state.pop(REMARK_MODAL_KEY, None)
    st.session_state.pop(ANALYSIS_TAB_KEY, None)


def _open_card(email: str, card_id: str) -> None:
    _close_card()
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
    running = job and job.card_id == card["id"] and job.status in {"running", "analyzing", "saving"}
    if running:
        detail = job.message
    elif schedule.daily_notice(card):
        # The cover has room for the headline only; the notepad carries the reason.
        detail = "Daily check failed · needs attention"
    else:
        detail = f"Last run · {format_time(runs[-1]['at'])}" if runs else "Ready for your first check"
    checks = "".join(f'<span class="sm-note-check{" is-filled" if i < len(runs) else ""}">{i + 1}</span>'
                     for i in range(store.CACHED_RUNS))
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
    if (card is None or selection.get("owner") != library_owner_id(email)
            or (card.get("shared") and card.get("owner_id") not in {library_owner_id(owner) for owner in allowed_emails()})):
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
    if job is None or job.id != job_id or job.status not in {"running", "analyzing", "saving"}:
        st.rerun(scope="app")
        return
    show_running_glow()
    st.progress(job.completed / len(job.posts), text=job.message)


def _render_background_job(email: str, key: str, job) -> None:
    """Keep a run reachable after its card is closed or removed."""
    if job is None:
        return
    if job.status in {"running", "analyzing", "saving"}:
        _run_progress(email, job.id)
    elif job.status == "save_failed":
        st.error(job.error)
        if st.button("Retry saving check", key=f"{key}_background_retry_save"):
            jobs.retry_save(email, job.id)
            st.rerun()


def _render_card_history(email: str, card: dict, state: dict) -> None:
    histories = state.setdefault("run_history", {})
    cached = histories.get(card["id"], {})
    recent = card.get("runs", [])
    version = (card.get("run_count", len(recent)), recent[-1]["id"] if recent else None,
               state.get("history_refresh", 0))
    if cached.get("version") != version:
        try:
            runs = (store.load_run_history(database.init_db(), email, card)
                    if card.get("run_count", len(recent)) > len(recent) else recent)
        except Exception:
            log.exception("Velocity history could not be loaded")
            st.warning("The full run history could not be refreshed. Previously loaded columns are kept. "
                       "Retry to load every saved date and count.")
            if st.button("Retry loading history", key=f"velocity_history_retry_{card['id']}"):
                st.rerun()
            if not cached:
                return  # Never present a rolling subset as complete history.
            # A recent subset can have gaps after several new collections. Keep
            # the complete last-loaded prefix until the archive is available,
            # rather than insert missing columns between visible ones later.
            runs = cached["runs"]
        else:
            runs = store.merge_run_history(cached.get("runs", []), runs)
            histories[card["id"]] = {"version": version, "runs": runs}
    else:
        runs = cached["runs"]
    card = {**card, "runs": runs}
    st.html(velocity_card_sheet(card))
    if not card.get("runs"):
        return
    if card["runs"][-1].get("metric") != METRIC:
        st.caption("Earlier checks tracked views. Run this card to start tracking likes + comments.")
        return
    latest = {row["post_id"]: row for row in card["runs"][-1].get("results", [])}
    missing = [(index, post, latest[post["id"]]) for index, post in enumerate(store.card_posts(card), 1)
               if post["id"] in latest and engagement_count(latest[post["id"]]) is None]
    if missing:
        with st.expander("Why counts are unavailable", expanded=True):
            for index, post, result in missing:
                st.write(f"Post {index}: {result.get('reason') or 'Both likes/reactions and comment totals are needed.'}")
                st.link_button(f"Open post {index} on Facebook", post["url"])
            st.caption("Status and percentage change need two consecutive likes + comments totals. "
                       "Likes includes all Facebook reactions. Missing counts are not treated as zero.")


def _close_remarks() -> None:
    st.session_state.pop(REMARK_MODAL_KEY, None)
    st.session_state[ANALYSIS_TAB_KEY] = True


@st.dialog("Remarks", width="large", on_dismiss=_close_remarks)
def _remarks_modal(email: str, key: str) -> None:
    from modules.auth import allowed_emails, verify_access
    try:
        if verify_access(st.session_state.get("auth_token", ""), allowed_emails()) != email:
            raise PermissionError()
    except Exception:
        _close_card()
        st.rerun()
    selection = st.session_state.get(MODAL_KEY, {})
    state = st.session_state[key]
    card = next((row for row in state["cards"] if row["id"] == selection.get("card_id") and not row.get("shared")), None)
    post = next((row for row in store.card_posts(card) if row["id"] == st.session_state.get(REMARK_MODAL_KEY)), None) if card else None
    if post is None or selection.get("owner") != library_owner_id(email):
        _close_remarks()
        st.rerun()
    with st.container(key="sm_velocity_modal"):
        st.caption(card["name"])
        st.link_button("Open Facebook post", post["url"])
        with st.form(f"{key}_remark_form_{card['id']}_{post['id']}"):
            remark = st.text_area("Remark", value=post.get("remark", ""), height=180,
                                  key=f"{key}_{card['id']}_remark_{post['id']}",
                                  max_chars=store.MAX_REMARK_CHARS, placeholder="Add a note about this post…")
            save = st.form_submit_button("Save remark", disabled=state["error"])
        if save:
            try:
                _update_owned_cards(state, store.save_remark(database.init_db(), email, card["id"], post["id"], remark))
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                log.exception("Velocity remark could not be saved")
                st.error("The remark could not be saved. Your text is still here; try Save remark again.")
            else:
                st.session_state[f"{key}_notice"] = "Remark saved."
                _close_remarks()
                st.rerun()
        if st.button("Cancel", key=f"{key}_cancel_remark"):
            _close_remarks()
            st.rerun()


def _render_remarks(card: dict, state: dict, card_key: str) -> None:
    posts = store.card_posts(card)
    if not posts:
        return
    by_id = {post["id"]: post for post in posts}
    post_id = st.selectbox("Post for remark", list(by_id), key=f"{card_key}_remark_post",
                           format_func=lambda ident: f"Post {list(by_id).index(ident) + 1} · {by_id[ident]['url']}")
    if st.button("Edit remarks", key=f"{card_key}_edit_remark", disabled=state["error"]):
        st.session_state[REMARK_MODAL_KEY] = post_id
        st.session_state[ANALYSIS_TAB_KEY] = True
        st.rerun()


def _render_card_tabs(email: str, card: dict, state: dict, card_key: str) -> None:
    velocity, analysis = st.tabs(["Velocity", "Analysis"],
                                 default="Analysis" if st.session_state.get(ANALYSIS_TAB_KEY) else "Velocity")
    with velocity:
        _render_card_history(email, card, state)
    with analysis:
        if card.get("runs"):
            st.caption(f"Latest run · {format_time(card['runs'][-1]['at'])}")
        st.html(analysis_table(card))
        st.caption("Sentiment of up to 20 readable comments per post. Percentages use successfully analyzed comments, "
                   "not all comments on Facebook. Negative includes anger and anxiety; positive includes joy. "
                   "Word size shows how many analyzed comments mention each topic.")
        if not card.get("shared"):
            _render_remarks(card, state, card_key)


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
        title, actions = st.columns([4, 1], vertical_alignment="center")
        title.subheader(_card_name(card, index))
        title.caption(f"{len(posts)} / {store.MAX_POSTS_PER_CARD} posts · Likes + comments (all reactions included)")
        if actions.button("Remove card", key=f"{key}_remove_{card['id']}", disabled=state["error"],
                          help="Remove this card for all users. Any current check finishes in the background, and saved checks are kept."):
            try:
                store.remove_workspace_card(database.init_db(), email, card)
                state["cards"] = [row for row in state["cards"] if row["id"] != card["id"]]
            except Exception:
                log.exception("Velocity card could not be removed")
                st.error("The card could not be removed. Refresh cards & checks before trying again.")
            else:
                state.get("run_history", {}).pop(card["id"], None)
                st.session_state[f"{key}_notice"] = "Card removed. Collected posts and checks are kept."
                _close_card()
                st.rerun()

        notice = schedule.daily_notice(card)
        if notice:
            st.warning(f"The automatic daily check saved no column: {notice}")

        if card.get("shared"):
            st.caption(f"{len(posts)} / {store.MAX_POSTS_PER_CARD} posts · Shared by another approved user · Any approved user can remove this card")
            _render_card_tabs(email, card, state, card_key)
            st.caption("The creator runs and edits this card. Use Refresh cards & checks to see their latest saved results.")
            return

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
                     help="Read engagement and up to 20 comments per post, analyze comment sentiment, and save the results."):
            try:
                provider = get_ai_provider(st.session_state.get("ai_provider"))
                model = st.session_state.get("gemini_model_name", get_ai_model(provider)) if provider == "gemini" else get_ai_model(provider)
                jobs.start_run(email, card["id"], hosted=hosted, provider=provider, model_name=model)
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                log.exception("Velocity run could not start")
                st.error("The run could not start. Refresh cards & checks and try again.")
            else:
                st.rerun()
        if job and job.card_id == card["id"]:
            if job.status in {"running", "analyzing", "saving"}:
                _run_progress(email, job.id)
            elif job.status in {"error", "save_failed"}:
                st.error(job.error)
                if job.status == "save_failed" and st.button("Retry saving check", key=f"{card_key}_retry_save"):
                    jobs.retry_save(email, job.id)
                    st.rerun()
            elif job.status == "complete" and job.run.get("warning"):
                st.warning(job.run["warning"])
        _render_card_tabs(email, card, state, card_key)
        if posts and not card.get("runs"):
            st.caption("Click Run to save engagement and analyze comments. Your Facebook connection from Scrape Data is used.")

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
    st.caption("Cards and saved results are shared with approved users. Any approved user can remove any card. Run and edit your own cards.")
    st.caption(schedule.schedule_caption())
    toolbar = st.container(horizontal=True, vertical_alignment="center", key="sm_velocity_toolbar")
    create_slot = toolbar.empty()
    refresh = toolbar.button("Refresh cards & checks", key=f"{key}_refresh")
    count_slot = toolbar.empty()
    job = jobs.latest_job(email)
    finished = bool(job and job.status == "complete" and st.session_state.get(f"{key}_seen_run") != job.id)
    owners = sorted(library_owner_id(owner) for owner in allowed_emails())
    if key not in st.session_state or refresh or finished or st.session_state[key].get("shared_owners") != owners:
        state = st.session_state.get(key, {"cards": []})
        if refresh:
            state["history_refresh"] = state.get("history_refresh", 0) + 1
        state["cards"] = [card for card in state["cards"] if not card.get("shared") or card.get("owner_id") in owners]
        try:
            db = database.init_db()
            state["cards"] = schedule.attach_status(db, email, store.load_workspace_cards(db, email))
            state["shared_owners"] = owners
            state["error"] = False
            if finished:
                st.session_state[f"{key}_seen_run"] = job.id
                found = sum(engagement_count(row) is not None for row in job.run["results"])
                st.session_state[f"{key}_notice"] = f"Check saved. Likes + comments totals available for {found} of {len(job.posts)} posts."
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
        _render_background_job(email, key, job)
        return

    with st.container(key="sm_velocity_grid"):
        for index, card in enumerate(cards, 1):
            _render_cover(email, card, index, key)
    if MODAL_KEY in st.session_state:
        if REMARK_MODAL_KEY in st.session_state:
            _remarks_modal(email, key)
        else:
            _card_modal(email, key, hosted=hosted)
    else:
        _render_background_job(email, key, job)
