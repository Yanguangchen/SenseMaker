"""Server settings shared by Firebase authentication and persistence."""

from __future__ import annotations

import os
from typing import Any, Dict


def get_setting(key: str, default: str = "") -> str:
    value = os.getenv(key, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        return str(st.secrets[key]).strip()
    except (KeyError, FileNotFoundError):
        return default


def firebase_credentials() -> Dict[str, Any]:
    """Read the existing Streamlit service-account table, if configured."""
    try:
        import streamlit as st

        return dict(st.secrets["firebase"])
    except (KeyError, FileNotFoundError):
        return {}
