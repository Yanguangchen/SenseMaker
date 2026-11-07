"""Browser-local auditory feedback that survives Streamlit reruns."""
from pathlib import Path
import json
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components


def _marker(event: dict[str, str]) -> None:
    st.html(f'<span data-sm-sound="{event["kind"]}" data-sm-sound-id="{event["id"]}" hidden></span>')


def notify_sound(kind: str) -> None:
    if kind not in {"success", "error"}:
        raise ValueError("Unsupported feedback sound")
    event = {"kind": kind, "id": uuid4().hex}
    st.session_state["audio_feedback_event"] = event
    _marker(event)


def install_audio_feedback() -> None:
    script = json.dumps(Path(__file__).with_name("theme").joinpath("audio-feedback.js").read_text()).replace("</", "<\\/")
    components.html(
        "<script>const doc = window.parent.document;"
        "if (!doc.getElementById('sm-audio-feedback')) {"
        "const s = doc.createElement('script'); s.id = 'sm-audio-feedback';"
        f"s.textContent = {script}; doc.head.appendChild(s); }}</script>", height=0,
    )
    if event := st.session_state.get("audio_feedback_event"):
        _marker(event)
