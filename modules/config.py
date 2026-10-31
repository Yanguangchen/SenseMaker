"""Server settings for authentication, persistence and AI providers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from dotenv import dotenv_values

AI_PROVIDERS = {"gemini": "Gemini", "openai": "ChatGPT"}
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"

# Values copied from our setup template are not configured credentials.
_SETTING_PLACEHOLDERS = {
    "FIREBASE_API_KEY": "your_firebase_web_api_key",
    "FIREBASE_APP_ID": "your_firebase_web_app_id",
    "FIREBASE_CREDENTIALS": "path/to/firebase-service-account.json",
}


def _setting_value(key: str, value: Any) -> str:
    value = str(value or "").strip()
    return "" if value == _SETTING_PLACEHOLDERS.get(key) else value


def load_environment(directory: Path | None = None) -> None:
    """Load local overrides before defaults, preserving configured server values.

    Empty or template values already loaded into a running Streamlit process may
    be filled from disk. Existing configured environment variables keep precedence.
    """
    directory = directory or Path(__file__).resolve().parents[1]
    for filename in (".env.local", ".env"):
        for key, value in dotenv_values(directory / filename).items():
            if _setting_value(key, value) and not _setting_value(key, os.getenv(key)):
                os.environ[key] = value


def get_setting(key: str, default: str = "") -> str:
    value = _setting_value(key, os.getenv(key))
    if value:
        return value
    try:
        import streamlit as st

        return _setting_value(key, st.secrets[key]) or default
    except (KeyError, FileNotFoundError):
        return default


def get_ai_provider(provider: str | None = None) -> str:
    value = (provider if provider is not None else get_setting("AI_PROVIDER", "gemini")).strip().lower()
    if value not in AI_PROVIDERS:
        raise ValueError("AI_PROVIDER must be gemini or openai.")
    return value


def get_ai_key(provider: str) -> str:
    """Read credentials on the server; never put them in a page widget."""
    provider = get_ai_provider(provider)
    if provider == "gemini":
        return get_setting("GEMINI_KEY") or get_setting("GEMINI_API_KEY")
    return get_setting("OPENAI_API_KEY")


def get_ai_model(provider: str) -> str:
    provider = get_ai_provider(provider)
    if provider == "gemini":
        return get_setting("GEMINI_MODEL", "auto")
    return get_setting("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)


def firebase_credentials() -> Dict[str, Any]:
    """Read the existing Streamlit service-account table, if configured."""
    try:
        import streamlit as st

        return dict(st.secrets["firebase"])
    except (KeyError, FileNotFoundError):
        return {}
