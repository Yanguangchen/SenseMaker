"""Google sign-in and server authorization for the shared Firebase project."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from firebase_admin import auth

from modules.config import get_setting
from modules.database import init_firebase


def allowed_emails() -> set[str]:
    """Use the same explicit allowlist as OYOSHI; an empty list grants no access."""
    return {
        email.lower()
        for email in re.split(r"[\s,]+", get_setting("FAQ_EDITOR_EMAILS"))
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    }


def web_config() -> dict[str, str]:
    """Only public Firebase web settings may cross into the sign-in component."""
    return {
        "apiKey": get_setting("FIREBASE_API_KEY"),
        "authDomain": get_setting("FIREBASE_AUTH_DOMAIN"),
        "projectId": get_setting("FIREBASE_PROJECT_ID"),
        "appId": get_setting("FIREBASE_APP_ID"),
    }


def verify_access(id_token: str, emails: set[str]) -> str:
    """Verify signature, project, expiry, revocation and approved email server-side."""
    if not emails or not isinstance(id_token, str) or not id_token:
        raise PermissionError("Sign in with an approved Google account.")
    identity = auth.verify_id_token(id_token, app=init_firebase(), check_revoked=True)
    email = identity.get("email")
    if (
        identity.get("email_verified") is not True
        or not identity.get("uid")
        or not isinstance(email, str)
        or email.strip().lower() not in emails
    ):
        raise PermissionError("This Google account does not have access to SenseMaker.")
    return email.strip().lower()


def _clear_session(message: str = "") -> None:
    import streamlit as st

    generation = st.session_state.get("auth_generation", 0) + 1
    # Remove loaded records, scraped posts and API keys when an identity leaves.
    st.session_state.clear()
    st.session_state["auth_generation"] = generation
    st.session_state["auth_message"] = message
    # A remounted component must also remove Firebase's persisted browser session.
    st.session_state["auth_reset_browser"] = True


def require_access() -> str:
    """Stop every dashboard rerun until the current account is authorized."""
    import streamlit as st
    import streamlit.components.v1 as components

    config = web_config()
    emails = allowed_emails()
    if not all(config.values()) or not emails:
        st.error("Sign-in is not configured. Ask the app administrator to finish setup.")
        st.stop()

    sign_in = components.declare_component(
        "sensemaker_google_sign_in", path=str(Path(__file__).parent / "google_sign_in")
    )
    with st.sidebar:
        event: Any = sign_in(
            config=config,
            clear_session=st.session_state.get("auth_reset_browser", False),
            key=f"google_sign_in_{st.session_state.get('auth_generation', 0)}",
            default=None,
        )
    if isinstance(event, dict) and event.get("event_id") != st.session_state.get("auth_event"):
        if event.get("type") == "sign_out":
            if st.session_state.get("auth_token") or st.session_state.get("auth_email"):
                _clear_session()
                st.rerun()
            # The browser has acknowledged sign-out, including on a fresh visit.
            st.session_state["auth_reset_browser"] = False
        elif event.get("id_token"):
            # Ignore browser-supplied emails; only a verified Firebase token is trusted.
            st.session_state["auth_token"] = event["id_token"]
            st.session_state["auth_message"] = ""
        st.session_state["auth_event"] = event.get("event_id")

    token = st.session_state.get("auth_token")
    if not token:
        st.write("Sign in with your approved Google account to continue.")
        if st.session_state.get("auth_message"):
            st.error(st.session_state["auth_message"])
        st.stop()

    try:
        email = verify_access(token, emails)
    except auth.ExpiredIdTokenError:
        # A background tab can outlive its ID token. Remount to obtain a fresh
        # token from Firebase's saved session without asking for Google login.
        st.session_state.pop("auth_token", None)
        st.session_state.pop("auth_event", None)
        st.session_state["auth_generation"] = st.session_state.get("auth_generation", 0) + 1
        st.rerun()
    except PermissionError as exc:
        _clear_session(str(exc))
        st.rerun()
    except (auth.InvalidIdTokenError, auth.UserDisabledError, auth.UserNotFoundError):
        _clear_session("Your sign-in could not be verified. Please sign in again.")
        st.rerun()
    except Exception:
        # Fail closed during verifier outages without destroying a valid login.
        st.error("Sign-in verification is temporarily unavailable. Please retry.")
        if st.button("Retry sign-in verification"):
            st.rerun()
        st.stop()

    previous_email = st.session_state.get("auth_email")
    if previous_email and previous_email != email:
        # Persistent Firebase sessions also synchronize account changes across
        # tabs. Never carry one account's loaded data into another account.
        generation = st.session_state.get("auth_generation", 0)
        event_id = st.session_state.get("auth_event")
        st.session_state.clear()
        st.session_state.update(
            auth_generation=generation, auth_event=event_id,
            auth_token=token, auth_email=email,
        )
        st.rerun()
    st.session_state["auth_email"] = email
    st.sidebar.caption(f"Signed in as {email}")
    if st.sidebar.button("Sign out", key="sensemaker_sign_out"):
        _clear_session()
        st.rerun()
    return email
