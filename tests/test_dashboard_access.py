"""Exercise the dashboard gate through Streamlit's actual script runner."""

import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from firebase_admin import auth
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

    def test_approved_identity_can_open_all_tabs(self):
        self.component.return_value = lambda **kwargs: {"id_token": "signed-token", "event_id": "login"}
        with patch("modules.auth.verify_access", return_value="editor@example.com") as verify:
            app = self.run_dashboard()
            self.assertEqual([tab.label for tab in app.tabs], ["Scrap data", "Facebook", "Reddit", "Online news", "Sentiment Analysis", "Sentiment Trends", "GenCite", "Analyze", "Recommendations", "Saved analyses"])
            verify.assert_called_once_with("signed-token", {"editor@example.com"})

    @patch.dict(os.environ, {"SENSEMAKER_HOSTED": "1"})
    def test_hosted_deployment_enables_configured_remote_login_and_keeps_collection_headless(self):
        self.component.return_value = lambda **kwargs: {"id_token": "signed-token", "event_id": "login"}
        from modules.facebook_session import FacebookSession
        with patch("modules.auth.verify_access", return_value="editor@example.com"), \
             patch("dashboard.IS_CLOUD", True), \
             patch("modules.facebook_remote.remote_facebook_configured", return_value=True), \
             patch("modules.facebook_remote.remote_facebook_session", return_value=FacebookSession()):
            app = self.run_dashboard()
            self.assertTrue(app.checkbox[0].value)
            self.assertTrue(app.checkbox[0].disabled)
            self.assertFalse(app.button(key="capture_fb_session_btn").disabled)
            self.assertTrue(app.button(key="collect_facebook_posts").disabled)

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
            self.assertTrue(app.session_state["auth_reset_browser"])
        self.database.assert_not_called()

    def test_browser_restores_login_into_a_new_streamlit_session(self):
        self.component.return_value = lambda **kwargs: {"id_token": "restored-token", "event_id": "restored"}
        with patch("modules.auth.verify_access", return_value="editor@example.com") as verify:
            self.run_dashboard()
            refreshed = self.run_dashboard()
        self.assertEqual(len(refreshed.tabs), 10)
        self.assertEqual(verify.call_count, 2)

    def test_server_sign_out_clears_browser_storage_before_another_login(self):
        calls = []

        def component(**kwargs):
            calls.append(kwargs)
            if kwargs["clear_session"]:
                return {"type": "sign_out", "event_id": "cleared"}
            if kwargs["key"].startswith("google_sign_in_0_"):
                return {"id_token": "signed-token", "event_id": "login"}
            return None

        self.component.return_value = component
        with patch("modules.auth.verify_access", return_value="editor@example.com"):
            app = self.run_dashboard()
            app.session_state["firestore_rows"] = ["private data"]
            app.button(key="sensemaker_sign_out").click().run()
            self.assertEqual(len(app.tabs), 0)
            self.assertNotIn("auth_token", app.session_state)
            self.assertNotIn("firestore_rows", app.session_state)
            self.assertFalse(app.session_state["auth_reset_browser"])
            app.run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.tabs), 0)
        self.assertTrue(any(call["clear_session"] for call in calls))

    def test_changed_firebase_settings_remount_the_component_without_granting_access(self):
        calls = []
        self.component.return_value = lambda **kwargs: calls.append(kwargs)
        with patch("modules.auth.web_config", return_value={
            "apiKey": "first-key", "authDomain": "example.firebaseapp.com",
            "projectId": "example", "appId": "public",
        }):
            app = self.run_dashboard()
        with patch("modules.auth.web_config", return_value={
            "apiKey": "corrected-key", "authDomain": "example.firebaseapp.com",
            "projectId": "example", "appId": "public",
        }):
            app.run(timeout=30)
        self.assertFalse(list(app.exception))
        self.assertNotEqual(calls[0]["key"], calls[1]["key"])
        self.assertEqual(len(app.tabs), 0)
        self.database.assert_not_called()

    def test_expired_token_requests_refresh_without_signing_out_or_losing_work(self):
        events = iter([{"id_token": "expired", "event_id": "old"}, None,
                       {"id_token": "fresh", "event_id": "renewed"}])
        calls = []

        def component(**kwargs):
            calls.append(kwargs)
            return next(events)

        self.component.return_value = component
        with patch("modules.auth.verify_access", side_effect=[
            auth.ExpiredIdTokenError("Expired", cause=None), "editor@example.com",
        ]):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py"))
            app.session_state["scrape_results"] = ["unsaved work"]
            app.run(timeout=30)
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.tabs), 0)
            self.database.assert_not_called()
            app.run(timeout=30)
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.tabs), 10)
            self.assertEqual(app.session_state["scrape_results"], ["unsaved work"])
        self.assertNotEqual(calls[0]["key"], calls[1]["key"])
        self.assertTrue(all(not call["clear_session"] for call in calls))

    def test_verifier_outage_blocks_access_but_preserves_login_for_retry(self):
        self.component.return_value = lambda **kwargs: {"id_token": "token", "event_id": "login"}
        with patch("modules.auth.verify_access", side_effect=[RuntimeError("offline"), "editor@example.com"]):
            app = self.run_dashboard()
            self.assertEqual(len(app.tabs), 0)
            self.database.assert_not_called()
            self.assertEqual(app.session_state["auth_token"], "token")
            app.button[0].click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.tabs), 10)

    def test_revoked_or_disabled_sessions_clear_saved_login_and_private_data(self):
        for error in (auth.RevokedIdTokenError("Revoked"),
                      auth.InvalidIdTokenError("Invalid"),
                      auth.UserDisabledError("Disabled"), auth.UserNotFoundError("Deleted")):
            with self.subTest(error=type(error).__name__):
                events = iter([{"id_token": "rejected", "event_id": "login"}, None])
                self.component.return_value = lambda **kwargs: next(events)
                with patch("modules.auth.verify_access", side_effect=error):
                    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py"))
                    app.session_state["firestore_rows"] = ["private data"]
                    app.run(timeout=30)
                self.assertFalse(list(app.exception))
                self.assertEqual(len(app.tabs), 0)
                self.assertNotIn("firestore_rows", app.session_state)
                self.assertTrue(app.session_state["auth_reset_browser"])
        self.database.assert_not_called()

    def test_cross_tab_account_change_clears_previous_accounts_loaded_data(self):
        event = {"id_token": "first", "event_id": "first-login"}
        self.component.return_value = lambda **kwargs: event
        with patch("modules.auth.verify_access", side_effect=lambda token, emails:
                   "editor@example.com" if token == "first" else "another@example.com"):
            app = self.run_dashboard()
            app.session_state["firestore_rows"] = ["first account's data"]
            event = {"id_token": "second", "event_id": "other-tab-login"}
            app.run(timeout=30)
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.tabs), 10)
            self.assertNotIn("firestore_rows", app.session_state)
            self.assertEqual(app.session_state["auth_email"], "another@example.com")
            self.assertEqual(app.session_state["auth_token"], "second")


if __name__ == "__main__":
    unittest.main()
