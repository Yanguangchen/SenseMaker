"""Drive the guided sentiment-analysis steps from AppTest scripts.

The Analysis tab shows one step at a time inside a focused modal, so each widget
is only in the element tree while its own step is open. ``ui(app, key)`` reveals
the owning step first, then returns the widget.
"""

STEP_BY_KEY = {
    "analysis_selection_mode": "Choose and edit data",
    "analysis_sources": "Choose and edit data",
    "analysis_datasets": "Choose and edit data",
    "refresh_analysis_datasets": "Choose and edit data",
    "thailand_watch_terms": "Analyze",
    "run_analysis_selected_btn": "Analyze",
}

KINDS = ("button", "multiselect", "radio", "selectbox", "slider", "text_area",
         "text_input", "checkbox", "number_input")


def _state(app, key):
    return app.session_state[key] if key in app.session_state else None


def open_step(app, key):
    """Open the guided modal on the step that owns this widget, if it owns one."""
    step = STEP_BY_KEY.get(key)
    if step is None:
        return app
    if _state(app, "focus_workspace_modal") != "analysis" or _state(app, "analysis_wizard_step") != step:
        app.session_state["focus_workspace_modal"] = "analysis"
        app.session_state["analysis_wizard_step"] = step
        app.run()
    return app


def ui(app, key):
    """Return the widget with this key, revealing its step first."""
    open_step(app, key)
    for kind in KINDS:
        try:
            return getattr(app, kind)(key=key)
        except KeyError:
            continue
    raise KeyError(key)
