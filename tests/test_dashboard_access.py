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
        self.stack.enter_context(patch("modules.facebook_links_ui.init_db", self.database))
        # The dataset browser loads metadata on entry; keep access tests offline.
        self.stack.enter_context(patch("modules.record_management_ui.init_db", self.database))
        self.stack.enter_context(patch("modules.record_management_ui.load_record_catalog", return_value=[]))
        self.usage_bar = self.stack.enter_context(patch("modules.browserbase_usage_ui.render_browserbase_usage_bar"))

    def run_dashboard(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py")).run(timeout=30)
        self.assertFalse(list(app.exception))
        return app

    def test_anonymous_request_cannot_render_tabs_or_access_database(self):
        self.component.return_value = lambda **kwargs: None
        app = self.run_dashboard()
        self.assertEqual(len(app.tabs), 0)
        self.database.assert_not_called()
        self.usage_bar.assert_not_called()

    def test_approved_identity_can_open_all_tabs(self):
        self.component.return_value = lambda **kwargs: {"id_token": "signed-token", "event_id": "login"}
        cards = [{"id": ident * 64, "name": name, "url": f"https://www.facebook.com/example/posts/{ident}"}
                 for ident, name in (("a", "Saved velocity post"), ("b", "Awaiting collection"))]
        post = {"url": cards[0]["url"], "raw_text": "Positive post", "analysis": {"sentiment": "Joy"},
                "checks": [{"at": "2026-11-01", "comments": 10}, {"at": "2026-11-02", "comments": 20}]}
        from modules.velocity_tracking import card_posts
        post_id = card_posts(cards[0])[0]["id"]
        cards[0]["runs"] = [{"at": f"2026-11-0{index + 1}", "results": [
            {"post_id": post_id, "views": views, "outcome": "ok"}]} for index, views in enumerate((100, 200))]
        with patch("modules.auth.verify_access", return_value="editor@example.com") as verify, \
                patch("modules.velocity_tracking.load_cards", return_value=cards), \
                patch("modules.velocity_tracking.load_card_records", return_value={post_id: post}):
            app = self.run_dashboard()
            self.assertEqual([tab.label for tab in app.tabs], ["Scrap data", "Collect data", "Saved Facebook links", "Clean & manage data", "Sentiment Analysis", "Analysis", "Settings", "Velocity Tracking", "Sentiment Trends", "Sentiment over time", "Border tension risk over time", "Content recommendations", "GenCite", "Analyze", "Recommendations", "Saved analyses", "Tutorial"])
            self.assertTrue(any('data-sm-view="Velocity Tracking"' in element.value for element in app.sidebar.markdown))
            velocity = next(tab for tab in app.tabs if tab.label == "Velocity Tracking")
            covers = [element.proto.body for element in velocity.get("html") if 'class="sm-note-cover"' in element.proto.body]
            self.assertEqual(len(covers), 2)
            self.assertIn("Saved velocity post", covers[0])
            self.assertIn("Awaiting collection", covers[1])
            tables = [element.proto.body for element in velocity.get("html") if 'aria-label="Velocity Tracker"' in element.proto.body]
            self.assertEqual(len(tables), 0)
            self.assertEqual(len([button for button in velocity.button if button.label.startswith("Open ")]), 2)
            self.assertNotIn("Velocity Tracker", {tab.label for tab in app.tabs})
            self.assertEqual([button.label for button in app.button if button.key and button.key.startswith("scrape_open_")],
                             ["Open Facebook →", "Open Oyoshi on Facebook →", "Open X →", "Open Online news →", "Open Custom website →", "Open Reddit"])
            verify.assert_called_once_with("signed-token", {"editor@example.com"})
            self.usage_bar.assert_called_once_with("editor@example.com")

    def test_hosted_deployment_enables_configured_remote_login_and_keeps_collection_headless(self):
        self.component.return_value = lambda **kwargs: {"id_token": "signed-token", "event_id": "login"}
        from modules.facebook_session import FacebookSession
        with patch("dashboard.IS_CLOUD", True), \
             patch.dict(os.environ, {"SENSEMAKER_HOSTED": "1"}), \
             patch("modules.auth.verify_access", return_value="editor@example.com"), \
             patch("modules.facebook_remote.remote_facebook_configured", return_value=True), \
             patch("modules.facebook_remote.remote_facebook_session", return_value=FacebookSession()):
            app = self.run_dashboard()
            # A source card opens its steps in a focused modal.
            self.assertNotIn("capture_fb_session_btn", [button.key for button in app.button])
            app.button(key="scrape_open_facebook").click().run(timeout=30)
            self.assertFalse(app.button(key="capture_fb_session_btn").disabled)
            # The later steps stay hidden until the Facebook connection is confirmed.
            self.assertNotIn("collect_facebook_posts", [button.key for button in app.button])
            self.assertTrue(app.button(key="facebook_wizard_step_next").disabled)
            app.session_state["facebook_wizard_step"] = "Collect posts"
            app.run(timeout=30)
            self.assertFalse(list(app.exception))
            self.assertTrue(app.checkbox(key="facebook_headless").value)
            self.assertTrue(app.checkbox(key="facebook_headless").disabled)
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
        self.assertEqual(len(refreshed.tabs), 17)
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
            self.assertEqual(len(app.tabs), 17)
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
            self.assertEqual(len(app.tabs), 17)

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
            self.assertEqual(len(app.tabs), 17)
            self.assertNotIn("firestore_rows", app.session_state)
            self.assertEqual(app.session_state["auth_email"], "another@example.com")
            self.assertEqual(app.session_state["auth_token"], "second")


if __name__ == "__main__":
    unittest.main()
