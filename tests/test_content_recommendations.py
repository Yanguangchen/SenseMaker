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


def recommendation(keyword="Acme delayed delivery help", ident="p1"):
    return {"concern": "Customers report late deliveries without updates.", "primary_keyword": keyword,
            "supporting_keywords": ["Acme delivery tracking", "Acme delivery support"],
            "search_intent": "Get an update on a late delivery", "title": "How to get an Acme delivery update",
            "format": "FAQ", "angle": "Acknowledge delays and explain the next step",
            "outline": ["Acknowledge the concern", "Provide a tracking route", "Explain how to get help"],
            "draft": "Waiting without an update is frustrating. Check [verify tracking link] for the latest status.",
            "facts_to_verify": ["Verify tracking link and support hours"],
            "measurement": "Compare delivery-related Anger/Anxiety shares on the same sources over two weeks.",
            "evidence": [{"record_id": ident, "source": "raw_text", "quote": "My Acme delivery is late"}]}


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
            else:
                resolve.assert_not_called()
                self.assertEqual(sent.get_header("Authorization"), "Bearer private-openai")
                self.assertEqual(body["model"], "gpt-5.4-mini")
                self.assertTrue(body["text"]["format"]["strict"])
                self.assertFalse(body["store"])
                prompt = body["input"]
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

    def test_unknown_ids_fabricated_quotes_parent_evidence_and_malformed_plans_are_rejected(self):
        mutations = [lambda r: r["evidence"][0].update(record_id="invented"),
                     lambda r: r["evidence"][0].update(quote="The product is unsafe"),
                     lambda r: r["evidence"][0].update(source="parent_text"),
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
        with self.assertRaises(recs.RecommendationError):
            recs._validate({"recommendations": [recommendation(), recommendation()]}, recs.source_records([row()]))

    def test_ranking_uses_distinct_cited_records_not_provider_scores_or_repeated_quotes(self):
        first = recommendation("Acme customer experience", "happy")
        second = recommendation()
        second["evidence"] *= 3
        result = recs._validate({"recommendations": [first, second]}, recs.source_records([row(), row("happy", "Joy")]))
        self.assertEqual(result[0]["primary_keyword"], second["primary_keyword"])
        self.assertEqual((result[0]["cited_records"], result[0]["concern_records"]), (1, 1))

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


if __name__ == "__main__":
    unittest.main()
