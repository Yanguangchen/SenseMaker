"""Process-local external-service fixtures for the browser E2E server only."""
from unittest.mock import patch
import streamlit as st
from tests.test_velocity_tracking import MemoryDB, post_url
from modules import velocity_tracking as store

EMAIL = 'owner@example.com'
db = MemoryDB()
# These patches live only in the dedicated test server process. Production code
# has no test-mode environment flag or authentication bypass.
for target, replacement in [
    ('modules.config.load_environment', lambda *a, **k: None),
    ('modules.database.init_db', lambda: db),
    ('modules.auth.allowed_emails', lambda: {EMAIL}),
    ('modules.auth.verify_access', lambda *a, **k: EMAIL),
    ('modules.velocity_tracking.firestore.transactional', lambda f: f),
    ('modules.velocity_schedule.ensure_scheduler_started', lambda: None),
    ('modules.velocity_schedule.attach_status', lambda db, email, cards: cards),
    ('modules.velocity_jobs.latest_job', lambda *a: None),
]:
    patch(target, replacement).start()

card = store.add_card(db, EMAIL, 'E2E shared card', post_url())[0]
store.add_card(db, EMAIL, 'PRIVATE OTHER CARD', post_url(2))
store.save_remark(db, EMAIL, card['id'], card['posts'][0]['id'], 'Watch this conversation')


def save_check(email=EMAIL, card_id=card['id'], **kwargs):
    current = next(c for c in store.load_cards(db, email) if c['id'] == card_id)
    number = current.get('run_count', 0) + 1
    store.save_run(db, email, card_id, {
        'id': f'{number:064x}', 'at': f'2026-11-09T01:{number:02d}:00+00:00',
        'metric': 'reactions_comments', 'results': [{
            'post_id': current['posts'][0]['id'], 'reactions': number * 100,
            'comments': 3, 'outcome': 'ok',
            'comment_sample': [{'text': 'PRIVATE RAW COMMENT'}],
            'comment_analysis': {'status': 'ok', 'analyzed_count': 2, 'collected_count': 2,
                                 'counts': {'positive': 1, 'neutral': 1, 'negative': 0},
                                 'keywords': [{'word': 'community', 'count': 2}]},
        }],
    })


for _ in range(10):
    save_check()
patch('modules.velocity_jobs.start_run', save_check).start()


def fixture_sign_in():
    st.header('Sign in to test workspace')
    if st.button('Sign in as test owner'):
        st.session_state['fixture_owner'] = True
        st.rerun()
    return None


patch('modules.auth.require_access', fixture_sign_in).start()
