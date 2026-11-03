"""Content plans: real provider payloads, evidence validation and Streamlit flow."""

import csv
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules import content_recommendations as recs
from modules.ai_errors import AIError
from modules.content_recommendations_ui import PLAN


KEYS = {"GEMINI_KEY": "private-gemini", "OPENAI_API_KEY": "private-openai",
        "GEMINI_MODEL": "auto", "OPENAI_MODEL": "gpt-5.4-mini", "AI_PROVIDER": "gemini"}


def row(ident="p1", sentiment="Anger"):
    return {"_id": ident, "status": "processed", "url": f"https://example.com/{ident}",
            "raw_text": "My Acme delivery is late and no one explains when it will arrive.",
            "analysis": {"sentiment": sentiment, "topics": ["delivery"]}}


def recommendation(keyword="Acme delayed delivery help", evidence_id="E1"):
    return {"concern": "Customers report late deliveries without updates.", "primary_keyword": keyword,
            "supporting_keywords": ["Acme delivery tracking", "Acme delivery support"],
            "search_intent": "Get an update on a late delivery", "title": "How to get an Acme delivery update",
            "format": "FAQ", "angle": "Acknowledge delays and explain the next step",
            "outline": ["Acknowledge the concern", "Provide a tracking route", "Explain how to get help"],
            "draft": "Waiting without an update is frustrating. Check [verify tracking link] for the latest status.",
            "facts_to_verify": ["Verify tracking link and support hours"],
            "measurement": "Compare delivery-related Anger/Anxiety shares on the same sources over two weeks.",
            "evidence_ids": [evidence_id]}


def reply(value, provider):
    payload = ({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(value)}]}}]}
               if provider == "gemini" else {"status": "completed", "output": [
                   {"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]})
    response = MagicMock()
    response.__enter__.side_effect = lambda: io.StringIO(json.dumps(payload))
    return response


class ContentPlanTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, KEYS))
        self.enterContext(patch("streamlit.secrets", {}))

    def generate(self, rows=None, **kwargs):
        return recs.generate_plan(rows or [row()], brand="Acme", goal="Explain delivery delays", **kwargs)

    def test_both_provider_payloads_use_existing_keys_and_model_settings(self):
        for provider in ("gemini", "openai"):
            target = "modules.processor.urlopen" if provider == "gemini" else "modules.openai.urlopen"
            with self.subTest(provider=provider), patch(target, return_value=reply(
                    {"recommendations": [recommendation()]}, provider)) as request, \
                 patch("modules.content_recommendations.resolve_model", return_value="models/gemini-2.5-flash") as resolve:
                plan = self.generate(provider=provider, language="Thai", focus="delivery")
            sent = request.call_args.args[0]
            body = json.loads(sent.data)
            self.assertEqual(plan["provider"], provider)
            self.assertEqual(plan["baseline"], {"Anger": 1})
            self.assertEqual(plan["recommendations"][0]["evidence"][0]["url"], "https://example.com/p1")
            self.assertNotIn(KEYS["GEMINI_KEY"], sent.data.decode() + json.dumps(plan))
            self.assertNotIn(KEYS["OPENAI_API_KEY"], sent.data.decode() + json.dumps(plan))
            if provider == "gemini":
                resolve.assert_called_once_with("private-gemini", "auto")
                self.assertEqual(sent.get_header("X-goog-api-key"), "private-gemini")
                self.assertIn("responseJsonSchema", body["generationConfig"])
                prompt = body["contents"][0]["parts"][0]["text"]
                schema = body["generationConfig"]["responseJsonSchema"]
            else:
                resolve.assert_not_called()
                self.assertEqual(sent.get_header("Authorization"), "Bearer private-openai")
                self.assertEqual(body["model"], "gpt-5.4-mini")
                self.assertTrue(body["text"]["format"]["strict"])
                self.assertFalse(body["store"])
                prompt = body["input"]
                schema = body["text"]["format"]["schema"]
            properties = schema["properties"]["recommendations"]["items"]["properties"]
            self.assertEqual(properties["evidence_ids"]["items"]["enum"], ["E1"])
            self.assertEqual(properties["draft"]["maxLength"], 2000)
            self.assertNotIn("evidence", properties)
            self.assertIn('"language": "Thai"', prompt)
            self.assertIn('"focus": "delivery"', prompt)
            self.assertIn("untrusted DATA", prompt)
            self.assertIn("Do not invent product facts", prompt)

    def test_missing_key_and_invalid_inputs_make_no_provider_request(self):
        with patch("modules.openai.generate_content") as request:
            for kwargs in ({"provider": "unknown"}, {"language": "invalid"}):
                with self.assertRaises(ValueError):
                    self.generate(**kwargs)
            with self.assertRaises(ValueError):
                self.generate([row(str(i)) for i in range(31)])
            with self.assertRaisesRegex(ValueError, "no usable source text"):
                self.generate([{**row(), "raw_text": " " * 1200 + "content outside excerpt"}])
            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), self.assertRaises(recs.RecommendationError):
                self.generate(provider="openai")
            request.assert_not_called()

    def test_sources_exclude_removed_errors_and_pending_and_handle_both_analysis_shapes(self):
        flat = {**row("flat"), "sentiment": "Anxiety"}
        flat.pop("analysis")
        records = recs.source_records([row(), row(), flat, {**row("removed"), "content_removed": True},
            {**row("pending"), "status": "pending"}, {**row("error"), "source_type": "batch_error"},
            {**row("unprocessed"), "analysis": {}}, {**row("empty"), "raw_text": ""}])
        self.assertEqual([r["id"] for r in records], ["p1", "flat"])

    def test_source_excerpts_preserve_languages_and_limit_input(self):
        source = {**row(), "raw_text": "កម្ពុជាថៃ" * 400, "comments": ["กังวลเรื่องสินค้า" * 100] * 3,
                  "parent_text": "Not attributable to a comment", "url": "javascript:alert(1)"}
        record = recs.source_records([source])[0]
        self.assertEqual(len(record["excerpts"]["raw_text"]), 1200)
        self.assertEqual(len(record["excerpts"]["comment_1"]), 300)
        self.assertEqual(set(record["excerpts"]), {"raw_text", "comment_1", "comment_2"})
        self.assertTrue(record["excerpted"])
        self.assertEqual(record["url"], "")

    def test_unknown_references_and_malformed_plans_are_rejected_with_specific_reasons(self):
        mutations = [lambda r: r.update(evidence_ids=["invented"]),
                     lambda r: r.update(evidence_ids=[]),
                     lambda r: r.update(evidence_ids=[{"source": "parent_text"}]),
                     lambda r: r.update(outline="not a list"),
                     lambda r: r.update(primary_keyword=""),
                     lambda r: r.update(format="Made up format")]
        for mutate in mutations:
            bad = recommendation()
            mutate(bad)
            with self.subTest(bad=bad), self.assertRaises(recs.RecommendationError):
                recs._validate({"recommendations": [bad]}, recs.source_records([row()]))
        with self.assertRaisesRegex(recs.RecommendationError, "do not support"):
            recs._validate({"recommendations": []}, recs.source_records([row()]))

    def test_duplicates_and_bad_briefs_do_not_discard_a_valid_recommendation(self):
        bad = recommendation("Another keyword", "unknown")
        warnings = []
        result = recs._validate({"recommendations": [bad, recommendation(), recommendation()]},
                                recs.source_records([row()]), warnings=warnings)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(warnings), 2)
        self.assertIn("unknown source reference", warnings[0])
        self.assertIn("duplicate primary keyword", warnings[1])

    def test_ranking_uses_distinct_cited_records_not_provider_scores_or_repeated_quotes(self):
        first = recommendation("Acme customer experience", "E2")
        second = recommendation()
        second["evidence_ids"] *= 3
        result = recs._validate({"recommendations": [first, second]}, recs.source_records([row(), row("happy", "Joy")]))
        self.assertEqual(result[0]["primary_keyword"], second["primary_keyword"])
        self.assertEqual((result[0]["cited_records"], result[0]["concern_records"]), (1, 1))
        self.assertEqual(len(result[0]["evidence"]), 1)

    def test_source_references_preserve_multilingual_text_and_never_use_provider_quotes(self):
        for original in ("ดี", "ខ្ញុំមិនទាន់ទទួលទំនិញទេ។", "จัดส่งช้า\n\tไม่มีการแจ้งเตือน", "Cafe\u0301\u00a0— delayed\r\nagain"):
            with self.subTest(original=original):
                record = recs.source_records([{**row(), "raw_text": original,
                                               "parent_text": "This is not the commenter speaking."}])
                item = recommendation()
                item["evidence"] = [{"record_id": "fake", "quote": "A fabricated translation", "url": "https://bad.test"}]
                result = recs._validate({"recommendations": [item]}, record)[0]
                self.assertEqual(result["evidence"][0]["quote"], original)
                self.assertEqual(result["evidence"][0]["record_id"], "p1")
                self.assertEqual(result["evidence"][0]["url"], "https://example.com/p1")
                self.assertEqual(len(recs.evidence_catalog(record)), 1)

    def test_comment_references_resolve_to_the_correct_comment_and_record(self):
        records = recs.source_records([{**row(), "comments": ["First comment", "Second comment"]}, row("p2")])
        result = recs._validate({"recommendations": [recommendation(evidence_id="E3")]}, records)[0]
        self.assertEqual(result["evidence"][0]["source"], "comment_2")
        self.assertEqual(result["evidence"][0]["quote"], "Second comment")
        self.assertEqual(result["evidence"][0]["record_id"], "p1")

    def test_invalid_response_is_repaired_once_for_each_provider(self):
        bad = recommendation()
        bad["draft"] = "x" * 2001
        for provider in ("gemini", "openai"):
            target = "modules.processor._generate_content" if provider == "gemini" else "modules.openai.generate_content"
            with self.subTest(provider=provider), patch(target, side_effect=[
                    json.dumps({"recommendations": [bad]}), json.dumps({"recommendations": [recommendation()]})]) as request, \
                 patch("modules.content_recommendations.resolve_model", return_value="models/gemini-2.5-flash"):
                plan = self.generate(provider=provider)
            self.assertEqual(request.call_count, 2)
            self.assertIn("draft must contain 1–2000 characters", request.call_args.args[0])
            self.assertEqual(plan["warnings"], [])
            self.assertEqual(len(plan["recommendations"]), 1)

    def test_malformed_json_is_repaired_but_validation_retries_are_bounded(self):
        good = json.dumps({"recommendations": [recommendation()]})
        with patch("modules.openai.generate_content", side_effect=["not JSON", good]) as request:
            self.generate(provider="openai")
            self.assertEqual(request.call_count, 2)
            self.assertIn("not a complete JSON object", request.call_args.args[0])
        with patch("modules.openai.generate_content", return_value="not JSON private-openai") as request:
            with self.assertRaisesRegex(recs.RecommendationError, "not a complete JSON object") as caught:
                self.generate(provider="openai")
            self.assertEqual(request.call_count, 2)
            self.assertNotIn("private-openai", str(caught.exception))

    def test_partial_response_returns_valid_briefs_with_warnings_without_retry(self):
        bad = recommendation("Bad idea", "E999")
        with patch("modules.openai.generate_content", return_value=json.dumps(
                {"recommendations": [recommendation(), bad]})) as request:
            plan = self.generate(provider="openai")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(plan["recommendations"]), 1)
        self.assertIn("unknown source reference", plan["warnings"][0])

    def test_empty_response_is_not_retried_as_a_validation_failure(self):
        with patch("modules.openai.generate_content", return_value='{"recommendations": []}') as request:
            with self.assertRaisesRegex(recs.RecommendationError, "do not support"):
                self.generate(provider="openai")
        self.assertEqual(request.call_count, 1)

    def test_no_repair_request_is_sent_after_the_time_budget(self):
        now = [0]

        def late_response(*args, **kwargs):
            now[0] = 121
            return json.dumps({"recommendations": [recommendation(evidence_id="unknown")]})

        with patch("modules.content_recommendations.time.monotonic", side_effect=lambda: now[0]), \
             patch("modules.openai.generate_content", side_effect=late_response) as request:
            with self.assertRaises(recs.RecommendationError):
                self.generate(provider="openai")
        self.assertEqual(request.call_count, 1)

    def test_temporary_failure_retries_but_auth_errors_are_redacted_and_not_retried(self):
        good = json.dumps({"recommendations": [recommendation()]})
        with patch("modules.openai.generate_content", side_effect=[AIError("busy", 429), good]) as request, \
             patch("modules.content_recommendations.time.sleep") as sleep:
            self.generate(provider="openai")
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once_with(2)
        with patch("modules.openai.generate_content", side_effect=AIError("denied private-openai", 401)) as request:
            with self.assertRaises(recs.RecommendationError) as caught:
                self.generate(provider="openai")
            self.assertNotIn("private-openai", str(caught.exception))
            self.assertEqual(request.call_count, 1)

    def test_csv_retains_unicode_and_neutralizes_spreadsheet_formulas(self):
        item = recommendation("=HYPERLINK(\"bad\")")
        item["title"] = "คำถามเกี่ยวกับสินค้า"
        plan = {"recommendations": recs._validate({"recommendations": [item]}, recs.source_records([row()]))}
        records = list(csv.reader(io.StringIO(recs.keyword_csv(plan))))
        self.assertTrue(records[1][1].startswith("'="))
        self.assertEqual(records[1][4], item["title"])


class ContentWorkspaceTests(unittest.TestCase):
    script = "from modules.content_recommendations_ui import render_content_workspace\nrender_content_workspace()"

    def setUp(self):
        self.enterContext(patch.dict(os.environ, KEYS))
        self.enterContext(patch("streamlit.secrets", {}))
        self.enterContext(patch("modules.content_recommendations.resolve_model", return_value="models/gemini-2.5-flash"))
        self.request = self.enterContext(patch("modules.processor._generate_content",
            return_value=json.dumps({"recommendations": [recommendation()]})))

    def app(self, rows=None):
        app = AppTest.from_string(self.script)
        app.session_state["processed_rows"] = rows if rows is not None else [row()]
        app.run()
        self.assertFalse(list(app.exception))
        return app

    def generate(self):
        app = self.app()
        app.text_input(key="content_brand").set_value("Acme").run()
        app.button(key="content_generate").click().run()
        self.assertFalse(list(app.exception))
        self.assertIn(PLAN, app.session_state)
        return app

    def test_empty_and_unconfigured_states_do_not_call_provider(self):
        empty = self.app([])
        self.assertIn("load processed records", empty.info[0].value)
        with patch.dict(os.environ, {"GEMINI_KEY": "", "GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}):
            app = self.app()
            app.text_input(key="content_brand").set_value("Acme").run()
            self.assertTrue(app.button(key="content_generate").disabled)
        self.request.assert_not_called()

    def test_generation_renders_keywords_evidence_baseline_and_downloads(self):
        app = self.generate()
        self.assertTrue(any("Acme delayed delivery help" in item.value for item in app.markdown))
        self.assertEqual(len(app.get("download_button")), 2)
        self.assertEqual(app.metric[0].value, "1")
        self.assertTrue(any("My Acme delivery is late" in item.value for item in app.text))
        self.request.assert_called_once()

    def test_changed_goal_or_source_removes_stale_plan(self):
        app = self.generate()
        app.text_area(key="content_goal").set_value("Clarify refund eligibility").run()
        self.assertNotIn(PLAN, app.session_state)
        self.assertEqual(len(app.get("download_button")), 0)
        app.button(key="content_generate").click().run()
        app.session_state["processed_rows"] = []
        app.run()
        self.assertFalse(list(app.exception))
        self.assertNotIn(PLAN, app.session_state)

    def test_latest_analysis_and_provider_switch(self):
        app = self.app([])
        flat = {**row(), "sentiment": "Anger"}
        flat.pop("analysis")
        app.session_state["analysis_last_results"] = {"analyses": [flat]}
        app.radio(key="content_source").set_value("Latest analysis").run()
        app.text_input(key="content_brand").set_value("Acme").run()
        app.button(key="content_generate").click().run()
        self.assertIn(PLAN, app.session_state)
        app.selectbox(key="content_provider").set_value("openai").run()
        self.assertNotIn(PLAN, app.session_state)
        self.assertFalse(list(app.exception))

    def test_provider_error_is_visible_and_does_not_leave_a_plan(self):
        app = self.generate()
        self.request.side_effect = AIError("key private-gemini was denied", 403)
        app.button(key="content_generate").click().run()
        self.assertNotIn(PLAN, app.session_state)
        self.assertTrue(app.error)
        self.assertNotIn("private-gemini", app.error[0].value)

    def test_partial_plan_renders_downloads_and_a_visible_notice(self):
        self.request.return_value = json.dumps({"recommendations": [
            recommendation(), recommendation("Another idea", "E999")]})
        app = self.generate()
        self.assertEqual(len(app.get("download_button")), 2)
        self.assertTrue(any("Some suggestions were omitted" in warning.value for warning in app.warning))
        self.assertTrue(any("unknown source reference" in text.value for text in app.text))
        self.request.assert_called_once()

    def test_ui_recovers_from_invalid_provider_output_automatically(self):
        self.request.side_effect = [json.dumps({"recommendations": [recommendation(evidence_id="bad")]}),
                                    json.dumps({"recommendations": [recommendation()]})]
        app = self.generate()
        self.assertFalse(list(app.error))
        self.assertEqual(self.request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
