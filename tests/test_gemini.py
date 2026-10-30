"""Account-specific model discovery and actionable failures without secret leakage."""

import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from streamlit.testing.v1 import AppTest

from modules.gemini import GeminiError, api_error, list_text_models, normalize_model_name, resolve_model
from modules.processor import _generate_content, analyze_posts_with_gemini


def response(payload):
    result = MagicMock()
    result.__enter__.side_effect = lambda: io.StringIO(json.dumps(payload))
    return result


def model(name, methods=None):
    return {"name": name, "supportedGenerationMethods": methods or ["generateContent"]}


class ModelTests(unittest.TestCase):
    def test_catalogue_paginates_and_only_offers_text_generation_models(self):
        replies = [response({"models": [model("models/gemini-2.5-flash"), model("models/gemini-3-image"),
                                       model("models/gemini-embedding", ["embedContent"])], "nextPageToken": "next"}),
                   response({"models": [model("models/gemini-3.6-flash"), model("models/gemini-3-pro")]})]
        with patch("modules.gemini.urlopen", side_effect=replies) as request:
            self.assertEqual(list_text_models("key"), ["models/gemini-2.5-flash", "models/gemini-3-pro", "models/gemini-3.6-flash"])
            self.assertIn("pageToken=next", request.call_args.args[0].full_url)
            self.assertEqual(request.call_args.args[0].get_header("X-goog-api-key"), "key")
            self.assertNotIn("key=", request.call_args.args[0].full_url)

    def test_auto_uses_available_stable_flash_and_explicit_missing_model_is_actionable(self):
        names = ["models/gemini-2.5-flash", "models/gemini-3.6-flash", "models/gemini-4-flash-preview",
                 "models/gemini-4-pro", "models/gemini-4-flash-cyber"]
        with patch("modules.gemini.list_text_models", return_value=names):
            self.assertEqual(resolve_model("key", "auto"), "models/gemini-3.6-flash")
            self.assertEqual(resolve_model("key", "gemini-2.5-flash"), "models/gemini-2.5-flash")
            with self.assertRaisesRegex(GeminiError, "not available.*Automatic"):
                resolve_model("key", "models/gemini-2.0-flash")

    def test_automatic_selection_never_silently_chooses_pro(self):
        with patch("modules.gemini.list_text_models", return_value=["models/gemini-4-pro"]):
            with self.assertRaisesRegex(GeminiError, "choose a model explicitly"):
                resolve_model("key", "auto")

    def test_http_errors_explain_cause_and_redact_the_key(self):
        for code, expected in ((400, "rejected"), (403, "Access was denied"), (404, "model is unavailable"), (429, "quota")):
            error = HTTPError("https://google.invalid", code, "error", {},
                              io.BytesIO(json.dumps({"error": {"message": "Bad key: private-key-value"}}).encode()))
            result = api_error(error, "private-key-value")
            self.assertIn(expected, str(result))
            self.assertNotIn("private-key-value", str(result))
            self.assertEqual(result.retryable, code == 429)

    def test_generate_error_keeps_google_diagnostic_and_safety_block_reason(self):
        error = HTTPError("https://google.invalid", 404, "missing", {}, io.BytesIO(json.dumps(
            {"error": {"message": "Model is not supported for generateContent."}}).encode()))
        with patch("modules.processor.urlopen", side_effect=error):
            with self.assertRaisesRegex(GeminiError, "HTTP 404.*generateContent"):
                _generate_content("hello", "key", "gemini-unknown")
        with patch("modules.processor.urlopen", return_value=response({"promptFeedback": {"blockReason": "SAFETY"}})):
            with self.assertRaisesRegex(GeminiError, "SAFETY"):
                _generate_content("hello", "key", "gemini-test")

    def test_model_validation_stops_before_generation_or_database_writes(self):
        import dashboard
        with patch("dashboard.init_db"), patch("dashboard.st.session_state", {"gemini_model_name": "auto"}), \
             patch("modules.processor.resolve_model", side_effect=GeminiError("Gemini HTTP 403: Access denied.")), \
             patch("modules.processor._generate_content") as generate, patch("dashboard.mark_post_processed") as save:
            with self.assertRaisesRegex(GeminiError, "403"):
                dashboard._process_selected_firestore_posts([{"_id": "a", "raw_text": "Hello"}], "key")
            generate.assert_not_called()
            save.assert_not_called()

    def test_permanent_generation_failure_is_not_retried_for_every_batch(self):
        rows = [{"_id": str(i), "raw_text": "hello"} for i in range(16)]
        with patch("modules.processor.resolve_model", return_value="models/gemini-test"), \
             patch("modules.processor._generate_content", side_effect=GeminiError("Gemini HTTP 403: Access denied.", 403)) as generate:
            with self.assertRaisesRegex(GeminiError, "HTTP 403"):
                analyze_posts_with_gemini(rows, api_key="key")
            self.assertEqual(generate.call_count, 1)


class GeminiSettingsTests(unittest.TestCase):
    script = "from dashboard import _render_gemini_settings\n_render_gemini_settings()\n"

    def test_refresh_replaces_unavailable_legacy_model_and_key_change_clears_catalogue(self):
        with patch("dashboard.list_text_models", return_value=["models/gemini-test-flash"]):
            app = AppTest.from_string(self.script)
            app.session_state["gemini_model_name"] = "models/gemini-retired"
            app.run()
            app.text_input[0].input("first-key").run()
            app.button(key="refresh_gemini_models").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.selectbox(key="gemini_model_name").value, "auto")
            self.assertIn("gemini-test-flash", app.selectbox(key="gemini_model_name").options)
            app.text_input[0].input("different-key").run()
            self.assertNotIn("gemini_available_models", app.session_state)

    def test_catalogue_failure_is_visible_after_rerun(self):
        with patch("dashboard.list_text_models", side_effect=GeminiError("Gemini HTTP 403: Access denied.")):
            app = AppTest.from_string(self.script).run()
            app.text_input[0].input("key").run()
            app.button(key="refresh_gemini_models").click().run()
            app.run()
            self.assertTrue(any("HTTP 403" in error.value for error in app.error))
            self.assertFalse(list(app.exception))

    def test_dashboard_analysis_failure_persists_actual_error_without_replacing_saved_analysis(self):
        with patch("modules.auth.require_access", return_value="editor@example.com"), \
             patch("modules.database.init_db"), patch("modules.database.mark_post_processed") as save, \
             patch("modules.processor.resolve_model", side_effect=GeminiError("Gemini HTTP 404: Model unavailable.")):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py"))
            app.session_state["firestore_rows"] = [{"_id": "a", "raw_text": "A Thai project", "status": "processed"}]
            app.run()
            key = next(e for e in app.text_input if e.label == "Gemini API key")
            key.input("fake-key").run()
            app.button(key="run_gemini_selected_btn").click().run()
            app.run()
            self.assertFalse(list(app.exception))
            self.assertTrue(any("HTTP 404" in e.value for e in app.error))
            self.assertEqual(app.session_state["gemini_last_results"]["processed"], 0)
            self.assertFalse(any("could not match" in w.value for w in app.warning))
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
