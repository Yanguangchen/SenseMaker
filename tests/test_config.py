"""Local credential loading must work without replacing deployment secrets."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.config import get_ai_key, load_environment


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


if __name__ == "__main__":
    unittest.main()
