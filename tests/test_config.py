"""Local credential loading must work without replacing deployment secrets."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.config import get_ai_key, get_setting, load_environment


class EnvironmentTests(unittest.TestCase):
    def test_local_keys_fill_blanks_from_an_already_running_dashboard(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "GEMINI_KEY": "", "OPENAI_API_KEY": " ",
        }, clear=True):
            directory = Path(folder)
            (directory / ".env").write_text("GEMINI_KEY=\nOPENAI_API_KEY=\n")
            (directory / ".env.local").write_text("GEMINI_KEY=local-gemini\nOPENAI_API_KEY=local-openai\n")
            load_environment(directory)
            self.assertEqual(get_ai_key("gemini"), "local-gemini")
            self.assertEqual(get_ai_key("openai"), "local-openai")

    def test_server_values_win_then_local_file_then_base_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "GEMINI_KEY": "deployment-gemini",
        }, clear=True):
            directory = Path(folder)
            (directory / ".env.local").write_text("GEMINI_KEY=local-gemini\nOPENAI_API_KEY=local-openai\nGEMINI_MODEL=\n")
            (directory / ".env").write_text("GEMINI_KEY=base-gemini\nOPENAI_API_KEY=base-openai\nGEMINI_MODEL=auto\n")
            load_environment(directory)
            self.assertEqual(get_ai_key("gemini"), "deployment-gemini")
            self.assertEqual(get_ai_key("openai"), "local-openai")
            self.assertEqual(os.environ["GEMINI_MODEL"], "auto")

    def test_missing_files_and_empty_or_unassigned_entries_do_not_create_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            directory = Path(folder)
            load_environment(directory)
            (directory / ".env").write_text("GEMINI_KEY=\nOPENAI_API_KEY\n")
            load_environment(directory)
            self.assertNotIn("GEMINI_KEY", os.environ)
            self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_firebase_template_overrides_do_not_hide_real_base_configuration(self):
        placeholders = {
            "FIREBASE_API_KEY": "your_firebase_web_api_key",
            "FIREBASE_APP_ID": "your_firebase_web_app_id",
            "FIREBASE_CREDENTIALS": "path/to/firebase-service-account.json",
            "FAQ_EDITOR_EMAILS": "editor1@example.com,editor2@example.com",
        }
        # Include values left in the process by an earlier dashboard run.
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, placeholders, clear=True):
            directory = Path(folder)
            (directory / ".env.local").write_text("\n".join(f"{key}={value}" for key, value in placeholders.items()))
            (directory / ".env").write_text(
                "FIREBASE_API_KEY=configured-key\nFIREBASE_APP_ID=configured-app\n"
                "FAQ_EDITOR_EMAILS=owner@example.com,colleague@example.com\n"
            )
            load_environment(directory)
            self.assertEqual(get_setting("FIREBASE_API_KEY"), "configured-key")
            self.assertEqual(get_setting("FIREBASE_APP_ID"), "configured-app")
            from modules.auth import allowed_emails
            self.assertEqual(allowed_emails(), {"owner@example.com", "colleague@example.com"})
            with patch("streamlit.secrets", {}):
                self.assertEqual(get_setting("FIREBASE_CREDENTIALS"), "")

    def test_template_only_firebase_configuration_is_not_reported_as_ready(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            directory = Path(folder)
            (directory / ".env.local").write_text(
                "FIREBASE_API_KEY=your_firebase_web_api_key\n"
                "FAQ_EDITOR_EMAILS=editor1@example.com,editor2@example.com\n"
            )
            load_environment(directory)
            self.assertNotIn("FIREBASE_API_KEY", os.environ)
            self.assertNotIn("FAQ_EDITOR_EMAILS", os.environ)
            with patch("streamlit.secrets", {"FIREBASE_API_KEY": "your_firebase_web_api_key"}):
                self.assertEqual(get_setting("FIREBASE_API_KEY"), "")

    def test_real_firebase_deployment_value_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "FIREBASE_API_KEY": "deployment-key",
        }, clear=True):
            directory = Path(folder)
            (directory / ".env.local").write_text("FIREBASE_API_KEY=local-key\n")
            load_environment(directory)
            self.assertEqual(get_setting("FIREBASE_API_KEY"), "deployment-key")


if __name__ == "__main__":
    unittest.main()
