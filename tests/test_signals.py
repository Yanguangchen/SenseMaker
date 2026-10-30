"""Prevent invented evidence, misattribution and silent loss in analysis batches."""

import copy
import io
import json
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules.processor import DEFAULT_MODEL, _generate_content, analyze_posts_with_gemini
from modules.signals import MAX_TEXT, is_flagged, normalize_analysis


def assessment(key="post_1", signal="boycott_call", text="Boycott the Thai project."):
    return {"_id": key, "translation": text, "sentiment": "Anger", "risk_score": 6, "topics": ["Thai project"],
            "monitor": {"thailand_relevance": "explicit", "signal_type": signal,
                        "target_kind": "country_general", "target_names": [], "confidence": "high",
                        "summary": "A call to boycott a Thai project, without a named project.",
                        "evidence": [{"source": "raw_text", "quote": "Thai", "purpose": "relevance"},
                                     {"source": "raw_text", "quote": text, "purpose": "signal"}]}}


class EvidenceTests(unittest.TestCase):
    def test_exact_quotes_support_a_reviewable_finding_without_inventing_a_project(self):
        result = normalize_analysis(assessment(), {"raw_text": "Boycott the Thai project."}, [], "model")
        self.assertTrue(is_flagged(result))
        self.assertEqual(result["monitor"]["target_kind"], "country_general")
        self.assertEqual(result["monitor"]["review_status"], "unreviewed")

    def test_fabricated_quotes_and_parent_only_calls_are_downgraded(self):
        result = normalize_analysis(assessment(), {"raw_text": "I love Thai food."}, [], "model")
        self.assertFalse(is_flagged(result))
        self.assertEqual(result["monitor"]["signal_type"], "unclear")
        item = assessment()
        for evidence in item["monitor"]["evidence"]:
            evidence["source"] = "parent_text"
        result = normalize_analysis(item, {"raw_text": "What is the source?", "parent_text": "Boycott the Thai project."}, [], "model")
        self.assertEqual(result["monitor"]["signal_type"], "unclear")
        self.assertEqual(result["monitor"]["confidence"], "low")

    def test_report_and_opposition_remain_distinct_from_advocacy(self):
        for kind, text in (("boycott_report", "The paper reports: Boycott the Thai project."),
                           ("boycott_opposition", "Do not boycott the Thai project.")):
            result = normalize_analysis(assessment(signal=kind, text=text), {"raw_text": text}, [], "model")
            self.assertEqual(result["monitor"]["signal_type"], kind)
            self.assertEqual(is_flagged(result), kind == "boycott_report")

    def test_named_targets_require_literal_target_evidence(self):
        item = assessment()
        item["monitor"].update(target_kind="named_project", target_names=["Invented Railway"])
        result = normalize_analysis(item, {"raw_text": "Boycott the Thai project."}, [], "model")
        self.assertEqual(result["monitor"]["target_names"], [])
        self.assertEqual(result["monitor"]["target_kind"], "unclear")

    def test_non_related_posts_cannot_count_as_thailand_signals(self):
        item = assessment()
        item["monitor"]["thailand_relevance"] = "not_related"
        result = normalize_analysis(item, {"raw_text": "Boycott the Thai project."}, [], "model")
        self.assertFalse(is_flagged(result))

    def test_truncated_records_are_identified_for_review(self):
        result = normalize_analysis(assessment(), {"raw_text": "Boycott the Thai project." + "a" * MAX_TEXT}, [], "model")
        self.assertTrue(result["monitor"]["input_truncated"])
        self.assertEqual(result["monitor"]["confidence"], "low")

    def test_khmer_and_thai_quotes_are_preserved_without_translation_matching(self):
        for text in ("កុំទិញទំនិញថៃ", "อย่าซื้อสินค้าไทย"):
            item = assessment(text=text)
            item["monitor"]["evidence"][0]["quote"] = text
            result = normalize_analysis(item, {"raw_text": text}, [], "model")
            self.assertEqual(result["monitor"]["evidence"][1]["quote"], text)


class AnalysisBatchTests(unittest.TestCase):
    def test_rest_transport_uses_request_local_key_and_rejects_partial_generation(self):
        reply = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}]}
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.side_effect = lambda: io.StringIO(json.dumps(reply))
        with patch("modules.processor.urlopen", return_value=response) as request:
            self.assertEqual(_generate_content("prompt", "local-key", DEFAULT_MODEL), "{}")
            sent = request.call_args.args[0]
            self.assertEqual(sent.get_header("X-goog-api-key"), "local-key")
            self.assertNotIn("local-key", sent.full_url)
            reply["candidates"][0]["finishReason"] = "MAX_TOKENS"
            with self.assertRaisesRegex(ValueError, "complete"):
                _generate_content("prompt", "local-key", DEFAULT_MODEL)

    def posts(self, count):
        return [{"_id": f"real-{i}", "raw_text": "Boycott the Thai project."} for i in range(count)]

    def test_shuffled_ids_map_correctly_but_unknown_duplicate_ids_never_fall_back_to_position(self):
        first, second = assessment("post_2"), assessment("post_1")
        first["translation"] = "second"
        second["translation"] = "first"
        with patch("modules.processor._generate_content", return_value=json.dumps({"results": [first, second]})):
            result = analyze_posts_with_gemini(self.posts(2), api_key="test")
        self.assertEqual(result["real-0"]["translation"], "first")
        with patch("modules.processor._generate_content", return_value=json.dumps({"results": [assessment("wrong"), assessment("wrong2")]})):
            self.assertEqual(analyze_posts_with_gemini(self.posts(2), api_key="test"), {})
        with patch("modules.processor._generate_content", return_value=json.dumps({"results": [assessment(), assessment()]})):
            self.assertEqual(analyze_posts_with_gemini(self.posts(1), api_key="test"), {})

    def test_large_jobs_batch_and_preserve_success_when_one_batch_fails(self):
        replies = [json.dumps({"results": [assessment(f"post_{i}") for i in range(1, 9)]}),
                   RuntimeError("Unavailable"), json.dumps({"results": [assessment()]})]
        with patch("modules.processor._generate_content", side_effect=replies) as generate:
            result = analyze_posts_with_gemini(self.posts(17), api_key="test")
        self.assertEqual(len(result), 9)
        self.assertIn("real-16", result)
        self.assertEqual(generate.call_count, 3)

    def test_collection_failures_are_not_sent_to_the_model(self):
        post = self.posts(1)[0]
        post["source_type"] = "batch_error"
        with patch("modules.processor._generate_content") as generate:
            self.assertEqual(analyze_posts_with_gemini([post], api_key="test"), {})
        generate.assert_not_called()

    def test_retries_rate_limits_but_not_other_failures(self):
        reply = json.dumps({"results": [assessment()]})
        with patch("modules.processor._generate_content", side_effect=[RuntimeError("HTTP 429"), reply]), patch("modules.processor.time.sleep") as sleep:
            self.assertEqual(len(analyze_posts_with_gemini(self.posts(1), api_key="test")), 1)
            sleep.assert_called_once_with(2)


class FindingsUITests(unittest.TestCase):
    def test_saved_findings_can_be_opened_without_nested_expanders(self):
        result = normalize_analysis(assessment(), {"raw_text": "Boycott the Thai project."}, [], "model")
        row = {"analysis": result, "url": "https://reddit.com/r/cambodia/comments/a/", "source_type": "reddit_post"}
        app = AppTest.from_string("from dashboard import _render_processed_records\n"
                                 f"_render_processed_records({repr([row])})\n").run()
        self.assertFalse(list(app.exception))
        self.assertEqual(app.subheader[0].value, "Explicit boycott call")

    def test_counts_filter_evidence_and_export_are_available(self):
        result = normalize_analysis(assessment(), {"raw_text": "Boycott the Thai project."}, [], "model")
        result.update(url="https://reddit.com/r/cambodia/comments/a/", source_type="reddit_post")
        other = copy.deepcopy(result)
        other["monitor"]["signal_type"] = "boycott_report"
        script = ("from dashboard import _render_signal_overview, _render_analysis_cards\n"
                  f"records = {repr([result, other])}\n"
                  "_render_analysis_cards(_render_signal_overview(records, 'findings'))\n")
        app = AppTest.from_string(script).run()
        self.assertFalse(list(app.exception))
        self.assertEqual([m.value for m in app.metric], ["2", "2", "1"])
        app.selectbox(key="findings").select("Boycott calls").run()
        self.assertFalse(list(app.exception))
        self.assertEqual(len(app.subheader), 1)
        self.assertTrue(any(t.value == "Boycott the Thai project." for t in app.text))


if __name__ == "__main__":
    unittest.main()
