"""Exercise the dashboard gate through Streamlit's actual script runner."""

import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class DashboardAccessTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("modules.auth.web_config", return_value={
            "apiKey": "public", "authDomain": "example.firebaseapp.com",
            "projectId": "example", "appId": "public",
        }))
        self.stack.enter_context(patch("modules.auth.allowed_emails", return_value={"editor@example.com"}))
        self.component = self.stack.enter_context(patch("streamlit.components.v1.declare_component"))
        self.database = self.stack.enter_context(patch("modules.database.init_db"))

    def run_dashboard(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py")).run(timeout=30)
        self.assertFalse(list(app.exception))
        return app

    def test_anonymous_request_cannot_render_tabs_or_access_database(self):
        self.component.return_value = lambda **kwargs: None
        app = self.run_dashboard()
        self.assertEqual(len(app.tabs), 0)
        self.database.assert_not_called()

    def test_approved_identity_can_open_both_tabs(self):
        self.component.return_value = lambda **kwargs: {"id_token": "signed-token", "event_id": "login"}
        with patch("modules.auth.verify_access", return_value="editor@example.com") as verify:
            app = self.run_dashboard()
            self.assertEqual([tab.label for tab in app.tabs], ["Scrap data", "Sentiment Analysis"])
            verify.assert_called_once_with("signed-token", {"editor@example.com"})

    @patch.dict(os.environ, {"SENSEMAKER_HOSTED": "1"})
    def test_hosted_deployment_keeps_browsers_headless_and_disables_interactive_login(self):
        self.component.return_value = lambda **kwargs: {"id_token": "signed-token", "event_id": "login"}
        with patch("modules.auth.verify_access", return_value="editor@example.com"):
            app = self.run_dashboard()
            self.assertTrue(app.checkbox[0].value)
            self.assertTrue(app.checkbox[0].disabled)
            self.assertTrue(app.button(key="capture_fb_session_btn").disabled)

    def test_denied_identity_loses_loaded_records_and_sees_no_tabs(self):
        # A new component instance after denial must not replay the rejected token.
        events = iter([{"id_token": "denied-token", "event_id": "login"}, None])
        self.component.return_value = lambda **kwargs: next(events)
        with patch("modules.auth.verify_access", side_effect=PermissionError("Access denied.")):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py"))
            app.session_state["firestore_rows"] = ["private data"]
            app.run(timeout=30)
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.tabs), 0)
            self.assertNotIn("firestore_rows", app.session_state)
            self.assertEqual(app.error[0].value, "Access denied.")
        self.database.assert_not_called()


if __name__ == "__main__":
    unittest.main()
