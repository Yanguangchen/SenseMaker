"""Authorization regression checks; no live Firebase writes or credentials needed."""

import os
import unittest
from unittest.mock import patch

from modules.auth import allowed_emails, verify_access, web_config


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.app = patch("modules.auth.init_firebase", return_value=object()).start()
        self.verify = patch("modules.auth.auth.verify_id_token").start()
        self.addCleanup(patch.stopall)
        self.verify.return_value = {
            "uid": "existing-google-user",
            "email": "Editor@Example.com",
            "email_verified": True,
        }

    def test_existing_editor_is_allowed_with_server_verified_identity(self):
        self.assertEqual(verify_access("signed-token", {"editor@example.com"}), "editor@example.com")
        self.verify.assert_called_once_with(
            "signed-token", app=self.app.return_value, check_revoked=True
        )

    def test_unlisted_and_unverified_accounts_are_denied(self):
        for changes in (
            {"email": "outsider@example.com"},
            {"email_verified": False},
            {"email_verified": "true"},
            {"email": None},
            {"uid": ""},
        ):
            with self.subTest(changes=changes):
                self.verify.return_value = {
                    "uid": "existing-google-user", "email": "editor@example.com",
                    "email_verified": True, **changes,
                }
                with self.assertRaises(PermissionError):
                    verify_access("signed-token", {"editor@example.com"})

    def test_empty_allowlist_or_invalid_token_never_authenticates(self):
        for token, emails in (("token", set()), ("", {"editor@example.com"}), (None, {"editor@example.com"})):
            with self.subTest(token=token, emails=emails):
                with self.assertRaises(PermissionError):
                    verify_access(token, emails)
        self.verify.assert_not_called()

    def test_expired_revoked_wrong_project_or_disabled_account_is_not_accepted(self):
        # The SDK validates these conditions; its failures must reach the gate.
        self.verify.side_effect = ValueError("Firebase rejected the token")
        with self.assertRaises(ValueError):
            verify_access("rejected-token", {"editor@example.com"})

    @patch("modules.auth.get_setting", return_value=" Editor@Example.com, other@example.com\nEDITOR@example.com ")
    def test_complete_shared_allowlist_is_normalized(self, _setting):
        self.assertEqual(allowed_emails(), {"editor@example.com", "other@example.com"})

    @patch.dict(os.environ, {
        "FIREBASE_API_KEY": "public-api-key", "FIREBASE_AUTH_DOMAIN": "example.firebaseapp.com",
        "FIREBASE_PROJECT_ID": "example-project", "FIREBASE_APP_ID": "public-app-id",
        "FIREBASE_PRIVATE_KEY": "must-never-reach-the-browser",
        "FAQ_EDITOR_EMAILS": "also-server-only@example.com",
    })
    def test_browser_configuration_contains_only_public_fields(self):
        self.assertEqual(web_config(), {
            "apiKey": "public-api-key", "authDomain": "example.firebaseapp.com",
            "projectId": "example-project", "appId": "public-app-id",
        })


class SessionTests(unittest.TestCase):
    def test_sign_out_clears_loaded_records_tokens_and_user_supplied_keys(self):
        from modules.auth import _clear_session

        state = {
            "auth_generation": 2, "auth_token": "token", "firestore_rows": ["private"],
            "scrape_results": ["private"], "gemini_key": "secret",
        }
        with patch("streamlit.session_state", state):
            _clear_session("Sign in again.")
        self.assertEqual(state, {
            "auth_generation": 3, "auth_message": "Sign in again.", "auth_reset_browser": True,
        })


if __name__ == "__main__":
    unittest.main()
