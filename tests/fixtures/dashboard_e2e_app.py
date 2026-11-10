"""Process-local browser E2E harness for the full authenticated dashboard."""

from contextlib import ExitStack
from unittest.mock import patch
import streamlit as st

import dashboard
from modules.facebook_session import FacebookSession
from modules import velocity_tracking as velocity_store
from tests.test_velocity_tracking import MemoryDB, post_url

EMAIL = "editor@example.com"
db = MemoryDB()

CATALOG_ROWS = [
    {
        "_id": "rec-1",
        "_version": "2026-11-09T00:00:00+00:00",
        "custom_title": "Border trade dataset",
        "title": "Border trade update",
        "url": "https://example.com/news/1",
        "source_type": "news_article",
        "platform": "google_news",
        "status": "processed",
        "scraped_at": "2026-11-09T00:00:00+00:00",
        "saved_at": "2026-11-09T00:00:00+00:00",
        "created_at": "2026-11-09T00:00:00+00:00",
        "raw_text": "Thailand border trade community dialogue and cooperation.",
        "analysis": {
            "sentiment": "Joy",
            "risk_score": 2,
            "translation": "Thailand border trade community dialogue and cooperation.",
            "topics": ["trade", "community"],
            "monitor": {
                "thailand_relevance": "explicit",
                "signal_type": "none",
                "target_kind": "country_general",
                "target_names": [],
                "confidence": "high",
                "summary": "Positive trade update",
                "evidence": [{"source": "raw_text", "quote": "Thailand", "purpose": "relevance"}],
            },
        },
    }
]


def _fixture_require_access():
    if not st.session_state.get("e2e_authenticated", True):
        st.header("Sign in to CrisisRadar")
        if st.button("Sign in as test editor"):
            st.session_state["e2e_authenticated"] = True
            st.rerun()
        return None
    st.session_state["auth_token"] = "e2e-token"
    st.session_state["auth_email"] = EMAIL
    st.sidebar.caption(f"Signed in as {EMAIL}")
    if st.sidebar.button("Sign out", key="sensemaker_sign_out"):
        st.session_state["e2e_authenticated"] = False
        st.rerun()
    return EMAIL


with ExitStack() as stack:
    for target, replacement in [
        ("modules.config.load_environment", lambda *a, **k: None),
        ("modules.database.init_db", lambda: db),
        ("modules.database.load_record_catalog", lambda _db=None: list(CATALOG_ROWS)),
        ("modules.auth.allowed_emails", lambda: {EMAIL}),
        ("modules.auth.verify_access", lambda *a, **k: EMAIL),
        ("modules.auth.require_access", _fixture_require_access),
        ("modules.velocity_tracking.firestore.transactional", lambda f: f),
        ("modules.velocity_schedule.ensure_scheduler_started", lambda: None),
        ("modules.velocity_schedule.attach_status", lambda _db, _email, cards: cards),
        ("modules.velocity_jobs.latest_job", lambda *a: None),
        ("modules.gencite_store.list_analyses", lambda _db, _email: []),
        ("modules.config.get_ai_key", lambda _p=None: "test-key"),
        ("dashboard._bridge_secrets_to_env", lambda: None),
        ("dashboard.load_environment", lambda: None),
        ("dashboard.init_db", lambda: db),
        ("dashboard.render_browserbase_usage_bar", lambda _email: None),
        ("dashboard.remote_facebook_session", lambda _email: FacebookSession("connected", "10001", "Test Operator")),
        ("dashboard.require_access", _fixture_require_access),
        ("dashboard._get_facebook_session", lambda _path=None: FacebookSession("connected", "10001", "Test Operator")),
        ("dashboard._load_sentiment_history", lambda: []),
        ("dashboard._load_news_history", lambda: []),
        ("dashboard._fetch_analysis_datasets", lambda sources: dashboard.dataset_options(CATALOG_ROWS, sources)),
    ]:
        stack.enter_context(patch(target, replacement))

    if not velocity_store.load_cards(db, EMAIL):
        velocity_store.add_card(db, EMAIL, "Border watch card", post_url(1))

    dashboard.main()
