"""Browser harness: real public entry route and real owner velocity workspace."""
from tests.fixtures.velocity_sharing_backend import EMAIL
import streamlit as st
import dashboard
dashboard._bridge_secrets_to_env = lambda: None
from modules.velocity_sharing import QUERY_KEY
from modules.velocity_tracking_ui import render_velocity_workspace

if QUERY_KEY in st.query_params or not st.session_state.get('fixture_owner'):
    dashboard.main()
else:
    st.set_page_config(layout='wide')
    render_velocity_workspace(EMAIL, hosted=False)
