"""Drive the guided sentiment-analysis steps from AppTest scripts.

The Analysis tab shows one step at a time inside a focused modal, so each widget
is only in the element tree while its own step is open. ``ui(app, key)`` reveals
the owning step first, then returns the widget.
"""

from modules.analysis_library import SOURCE_LABELS

STEP_BY_KEY = {
    "analysis_selection_mode": "Choose and edit data",
    "analysis_sources": "Choose and edit data",
    **{f"analysis_source_{source}": "Choose and edit data" for source in SOURCE_LABELS},
    "analysis_datasets": "Choose and edit data",
    "refresh_analysis_datasets": "Choose and edit data",
    "thailand_watch_terms": "Analyze",
    "run_analysis_selected_btn": "Analyze",
}

KINDS = ("button", "multiselect", "radio", "selectbox", "slider", "text_area",
         "text_input", "checkbox", "number_input")


class _SourceCheckboxes:
    """Group the source-filter checkboxes behind the common AppTest selection API."""

    def __init__(self, app):
        self._app = app

    def _box(self, source: str):
        return self._app.checkbox(key=f"analysis_source_{source}")

    @property
    def value(self) -> list[str]:
        return [source for source in SOURCE_LABELS if self._box(source).value]

    def select(self, value: str):
        self._box(value).check()
        return self._app

    def set_value(self, values: list[str]):
        chosen = set(values)
        for source in SOURCE_LABELS:
            box = self._box(source)
            if source in chosen:
                box.check()
            else:
                box.uncheck()
        return self._app


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
    if key == "analysis_sources":
        return _SourceCheckboxes(app)
    for kind in KINDS:
        try:
            return getattr(app, kind)(key=key)
        except KeyError:
            continue
    raise KeyError(key)
