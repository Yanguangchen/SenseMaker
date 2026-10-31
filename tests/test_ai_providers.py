"""Provider switching, server-only credentials and OpenAI failure handling."""

import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from streamlit.testing.v1 import AppTest

from modules.config import get_ai_key, get_ai_provider
from modules.openai import OpenAIError, api_error, generate_content
from modules.processor import analyze_posts, process_pending_posts


def response(payload):
    result = MagicMock()
    result.__enter__.side_effect = lambda: io.StringIO(json.dumps(payload))
    return result


def result_text():
    return json.dumps({"results": [{"_id": "post_1", "translation": "Hello", "sentiment": "Neutral",
                                    "risk_score": 1, "topics": [], "monitor": {
                                        "thailand_relevance": "not_related", "signal_type": "none",
                                        "target_kind": "unclear", "target_names": [], "confidence": "low",
                                        "summary": "A greeting.", "evidence": [],
                                    }}]})


def completed(text="{}"):
    return {"status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": text}]},
    ]}


class OpenAITransportTests(unittest.TestCase):
    def test_responses_request_keeps_key_in_header_and_requests_json_without_storage(self):
        with patch("modules.openai.urlopen", return_value=response(completed())) as request:
            self.assertEqual(generate_content("Return JSON", "private-key", "configured-model", timeout=30), "{}")
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(sent.get_header("Authorization"), "Bearer private-key")
        self.assertNotIn("private-key", sent.full_url + sent.data.decode())
        self.assertEqual(json.loads(sent.data), {"model": "configured-model", "input": "Return JSON",
                                               "store": False, "text": {"format": {"type": "json_object"}}})
        self.assertEqual(request.call_args.kwargs["timeout"], 30)

    def test_incomplete_refused_empty_and_malformed_responses_are_rejected(self):
        cases = [
            ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "complete.*max_output_tokens"),
            ({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}, "declined"),
            (completed(""), "no analysis text"),
            ({"status": "completed", "output": {}}, "invalid"),
            ([], "invalid"),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload), patch("modules.openai.urlopen", return_value=response(payload)):
                with self.assertRaisesRegex(OpenAIError, expected):
                    generate_content("JSON", "private-key", "configured-model")

    def test_errors_redact_secrets_and_preserve_retry_status(self):
        for code in (400, 401, 403, 404, 429, 503):
            error = HTTPError("https://api.openai.com", code, "error", {}, io.BytesIO(json.dumps(
                {"error": {"message": "Invalid private-key and sk-proj-someothersecret"}}).encode()))
            result = api_error(error, "private-key")
            self.assertIn(f"HTTP {code}", str(result))
            self.assertNotIn("private-key", str(result))
            self.assertNotIn("someothersecret", str(result))
            self.assertEqual(result.retryable, code in (429, 503))
        with patch("modules.openai.urlopen", side_effect=URLError("Cannot connect: private-key")):
            with self.assertRaisesRegex(OpenAIError, "connection failed") as caught:
                generate_content("JSON", "private-key", "configured-model")
            self.assertNotIn("private-key", str(caught.exception))
        with patch("modules.openai.urlopen", side_effect=TimeoutError()):
            with self.assertRaisesRegex(OpenAIError, "timed out after 30 seconds"):
                generate_content("JSON", "private-key", "configured-model", timeout=30)


class ProviderTests(unittest.TestCase):
    def test_server_environment_and_streamlit_secrets_support_both_providers(self):
        with patch.dict(os.environ, {"GEMINI_KEY": " env-gemini ", "OPENAI_API_KEY": " env-openai "}), \
             patch("streamlit.secrets", {"GEMINI_KEY": "secret-gemini", "OPENAI_API_KEY": "secret-openai"}):
            self.assertEqual(get_ai_key("gemini"), "env-gemini")
            self.assertEqual(get_ai_key("openai"), "env-openai")
        with patch.dict(os.environ, {}, clear=True), \
             patch("streamlit.secrets", {"GEMINI_KEY": "secret-gemini", "OPENAI_API_KEY": "secret-openai"}):
            self.assertEqual(get_ai_key("gemini"), "secret-gemini")
            self.assertEqual(get_ai_key("openai"), "secret-openai")
            self.assertEqual(get_ai_provider(), "gemini")
        with patch.dict(os.environ, {"GEMINI_KEY": "", "GEMINI_API_KEY": "alias-key"}), patch("streamlit.secrets", {}):
            self.assertEqual(get_ai_key("gemini"), "alias-key")

    def test_provider_selection_uses_only_its_server_key_and_records_provenance(self):
        with patch.dict(os.environ, {"GEMINI_KEY": "server-gemini", "OPENAI_API_KEY": "server-openai",
                                     "OPENAI_MODEL": "configured-openai", "GEMINI_MODEL": "auto"}), \
             patch("modules.processor.resolve_model", return_value="models/gemini-test") as resolve, \
             patch("modules.processor._generate_content", return_value=result_text()) as gemini, \
             patch("modules.processor.generate_openai_content", return_value=result_text()) as openai:
            rows = [{"_id": "real-id", "raw_text": "Hello"}]
            result = analyze_posts(rows, provider="openai")
            self.assertEqual(result["real-id"]["provider"], "openai")
            self.assertEqual(result["real-id"]["model"], "configured-openai")
            self.assertEqual(openai.call_args.args[1:3], ("server-openai", "configured-openai"))
            resolve.assert_not_called()
            gemini.assert_not_called()
            result = analyze_posts(rows, provider="gemini")
            self.assertEqual(result["real-id"]["provider"], "gemini")
            self.assertEqual(gemini.call_args.args[1:3], ("server-gemini", "models/gemini-test"))
            resolve.assert_called_once_with("server-gemini", "auto")

    def test_openai_retries_transient_errors_and_stops_on_permanent_errors(self):
        rows = [{"_id": "real-id", "raw_text": "Hello"}]
        with patch("modules.processor.generate_openai_content", side_effect=[OpenAIError("Quota", 429), result_text()]), \
             patch("modules.processor.time.sleep") as sleep:
            self.assertEqual(len(analyze_posts(rows, provider="openai", api_key="key")), 1)
            sleep.assert_called_once_with(2)
        with patch("modules.processor.generate_openai_content", side_effect=OpenAIError("Denied", 403)) as generate:
            with self.assertRaisesRegex(OpenAIError, "Denied"):
                analyze_posts([{"_id": str(i), "raw_text": "Hello"} for i in range(16)], provider="openai", api_key="key")
            generate.assert_called_once()

    def test_missing_openai_key_never_falls_back_to_gemini_or_writes_records(self):
        import dashboard
        with patch.dict(os.environ, {"GEMINI_KEY": "server-gemini", "OPENAI_API_KEY": ""}), \
             patch("streamlit.secrets", {}), patch("dashboard.init_db"), \
             patch("dashboard.st.session_state", {}), patch("dashboard.persist_analysis_run") as save, \
             patch("modules.processor.generate_openai_content") as generate:
            with self.assertRaisesRegex(OpenAIError, "ChatGPT is not configured"):
                dashboard._process_selected_firestore_posts([{"_id": "a", "raw_text": "Hello"}], provider="openai")
            generate.assert_not_called()
            save.assert_not_called()

    def test_pipeline_honors_default_provider_from_environment(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "pipeline-key"}), \
             patch("modules.processor.get_pending_posts", return_value=[{"_id": "a", "raw_text": "Hello"}]), \
             patch("modules.processor.generate_openai_content", return_value=result_text()) as generate, \
             patch("modules.processor.persist_analysis_run") as save:
            self.assertEqual(process_pending_posts(MagicMock()), 1)
            self.assertEqual(generate.call_args.args[1], "pipeline-key")
            self.assertEqual(save.call_args.args[2]["a"]["provider"], "openai")
            self.assertEqual(save.call_args.kwargs["provider"], "openai")


class ProviderUITests(unittest.TestCase):
    script = "from dashboard import _render_ai_settings\n_render_ai_settings()\n"

    def test_switching_providers_never_renders_or_stores_keys_in_widgets(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "gemini", "GEMINI_KEY": "private-gemini", "OPENAI_API_KEY": "private-openai"}):
            app = AppTest.from_string(self.script).run()
            self.assertEqual(app.selectbox(key="ai_provider").value, "gemini")
            self.assertEqual(app.selectbox(key="ai_provider").options, ["Gemini", "ChatGPT"])
            for provider in ("openai", "gemini"):
                app.selectbox(key="ai_provider").select(provider).run()
                self.assertFalse(list(app.exception))
                self.assertEqual(len(app.text_input), 0)
                visible = str(app) + repr(app.session_state.filtered_state)
                self.assertNotIn("private-gemini", visible)
                self.assertNotIn("private-openai", visible)

    def test_dashboard_switch_routes_analysis_and_disables_unconfigured_provider(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "gemini", "GEMINI_KEY": "private-gemini", "OPENAI_API_KEY": "private-openai"}), \
             patch("modules.config.load_environment"), \
             patch("streamlit.secrets", {}), patch("modules.auth.require_access", return_value="editor@example.com"), \
             patch("modules.database.init_db"), patch("modules.sentiment_history.persist_analysis_run", return_value=None) as save, \
             patch("modules.openai.urlopen", return_value=response(completed(result_text()))) as request, \
             patch("modules.processor._generate_content") as gemini:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py"), default_timeout=30)
            app.session_state["firestore_rows"] = [{"_id": "a", "raw_text": "Hello", "status": "pending"}]
            app.run()
            app.selectbox(key="ai_provider").select("openai").run()
            self.assertEqual(app.button(key="run_analysis_selected_btn").label, "Run ChatGPT on selected records")
            app.button(key="run_analysis_selected_btn").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state["analysis_last_results"]["processed"], 1)
            self.assertEqual(save.call_args.args[2]["a"]["provider"], "openai")
            self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer private-openai")
            gemini.assert_not_called()
            os.environ["OPENAI_API_KEY"] = ""
            app.run()
            self.assertTrue(app.button(key="run_analysis_selected_btn").disabled)
            self.assertTrue(any("ChatGPT is not configured" in warning.value for warning in app.warning))


if __name__ == "__main__":
    unittest.main()
