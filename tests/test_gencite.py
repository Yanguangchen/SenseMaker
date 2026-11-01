"""GenCite share of voice: providers, aggregation, recommendations, storage and UI."""

import io
import json
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from streamlit.testing.v1 import AppTest

from modules import gencite
from modules import gencite_store as store
from modules.ai_errors import AIError

KEYS = {"GEMINI_KEY": "private-gemini", "GEMINI_API_KEY": "", "OPENAI_API_KEY": "private-openai",
        "GENCITE_GEMINI_MODEL": "", "GENCITE_OPENAI_MODEL": ""}


def response(payload):
    result = MagicMock()
    result.__enter__.side_effect = lambda: io.StringIO(json.dumps(payload))
    return result


def gemini_reply(value):
    return response({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(value)}]}}]})


def openai_reply(value):
    return response({"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]})


def variations(count=50):
    return {"variations": [{"text": f"query {i}", "tag": gencite.ALLOWED_TAGS[i % 10]} for i in range(count)]}


def numbered_queries(prompt):
    return [json.loads(line.split(": ", 1)[1]) for line in prompt.splitlines() if re.match(r"^\d+: \"", line)]


def recommendation(i, indexes=(0,)):
    return {"title": f"Guide {i}", "question": f"Question {i}?", "cluster": "Pricing", "audience": "Buyers",
            "angle": "Comparison", "supportingTerms": ["a", "b", "c"], "requirements": ["w", "x", "y", "z"],
            "confidence": "medium", "relevance": 80, "freshness": 50, "sourceQueryIndexes": list(indexes)}


def keyword(text, coverage="missing", indexes=()):
    return {"keyword": text, "tag": "Pricing", "coverage": coverage, "rationale": "Untested intent.",
            "opportunity": 90, "relevance": 70, "confidence": "high", "relatedQueryIndexes": list(indexes)}


class FakeProviders:
    """Answers per provider: Gemini mentions the brand in 2 of 5 answers, ChatGPT in all 5."""

    def __init__(self, fail=None, keywords=None):
        self.calls, self.fail, self.keywords = [], fail or {}, keywords

    def __call__(self, provider, api_key, model, prompt, schema, **options):
        self.calls.append(SimpleNamespace(provider=provider, key=api_key, model=model, prompt=prompt,
                                          schema=schema, options=options))
        properties = schema["properties"]
        if "variations" in properties:
            return variations()
        if "recommendations" in properties:
            return {"recommendations": [recommendation(i) for i in range(5)]}
        if "keywords" in properties:
            return {"keywords": self.keywords or [keyword(f"new idea {i}") for i in range(8)]}
        queries = numbered_queries(prompt)
        if self.fail.get(provider) and self.fail[provider](queries):
            raise AIError(f"{provider} failed with {api_key}", 403)
        if provider == "gemini":
            answers = ["Acme leads", "acme is good", "Beta wins", "nothing", "none"]
        else:
            answers = ["ACM Labs is great"] * 4 + ["Beta and ACME"]
        return {"results": [{"queryIndex": i, "answers": answers} for i in range(len(queries))]}


class TransportTests(unittest.TestCase):
    def test_gemini_requests_structured_json_disables_thinking_and_keeps_key_in_header(self):
        with patch.dict(os.environ, KEYS), patch("streamlit.secrets", {}), \
             patch("modules.processor.urlopen", return_value=gemini_reply(variations())) as request:
            outcome = gencite.generate_variations("crm software")
        sent = request.call_args.args[0]
        body = json.loads(sent.data)
        self.assertTrue(sent.full_url.endswith("/models/gemini-2.5-flash:generateContent"))
        self.assertEqual(sent.get_header("X-goog-api-key"), "private-gemini")
        self.assertNotIn("private-gemini", sent.full_url + sent.data.decode())
        config = body["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertEqual(config["thinkingConfig"], {"thinkingBudget": 0})
        self.assertEqual(config["responseJsonSchema"]["properties"]["variations"]["minItems"], 50)
        self.assertIn('"crm software"', body["contents"][0]["parts"][0]["text"])
        self.assertEqual((outcome["provider"], outcome["model"], outcome["fallback"]),
                         ("gemini", "gemini-2.5-flash", False))
        self.assertEqual(len(outcome["variations"]), 50)

    def test_thinking_stays_on_for_other_gemini_families(self):
        self.assertTrue(gencite._disables_thinking("models/gemini-2.5-flash-lite"))
        for model in ("models/gemini-3-flash", "models/gemini-2.5-pro"):
            with self.subTest(model=model), patch("modules.processor.urlopen", return_value=gemini_reply({})) as request:
                gencite._call_json("gemini", "key", model, "p", {"type": "object"}, temperature=0.5,
                                   max_output_tokens=10, timeout=5)
                self.assertNotIn("thinkingConfig", json.loads(request.call_args.args[0].data)["generationConfig"])

    def test_openai_uses_strict_schema_and_retries_without_unsupported_temperature(self):
        rejected = HTTPError("https://api.openai.com", 400, "bad", {}, io.BytesIO(json.dumps(
            {"error": {"message": "Unsupported parameter: 'temperature' is not supported with this model."}}).encode()))
        schema = {"type": "object", "properties": {}}
        with patch("modules.openai.urlopen", side_effect=[rejected, openai_reply({"ok": True})]) as request:
            parsed = gencite._call_json("openai", "key", "gpt-5.4-mini", "prompt", schema, temperature=0.9,
                                        max_output_tokens=50000, timeout=30)
        self.assertEqual(parsed, {"ok": True})
        first, second = (json.loads(call.args[0].data) for call in request.call_args_list)
        self.assertEqual(first["temperature"], 0.9)
        self.assertNotIn("temperature", second)
        self.assertEqual(second["max_output_tokens"], 32768)
        self.assertEqual(second["text"]["format"], {"type": "json_schema", "name": "sensemaker_response",
                                                    "schema": schema, "strict": True})
        self.assertFalse(second["store"])

    def test_variations_use_chatgpt_when_only_it_is_configured_then_local_templates(self):
        with patch.dict(os.environ, {**KEYS, "GEMINI_KEY": "", "GEMINI_API_KEY": ""}), patch("streamlit.secrets", {}), \
             patch("modules.openai.urlopen", return_value=openai_reply(variations())) as request:
            outcome = gencite.generate_variations("crm software")
            self.assertEqual((outcome["provider"], outcome["model"]), ("openai", "gpt-4.1-mini"))
            self.assertEqual(json.loads(request.call_args.args[0].data)["model"], "gpt-4.1-mini")
        with patch.dict(os.environ, {"GEMINI_KEY": "", "GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}), \
             patch("streamlit.secrets", {}):
            outcome = gencite.generate_variations("crm software")
        self.assertTrue(outcome["fallback"])
        self.assertEqual(len(outcome["variations"]), 50)
        self.assertIn("No AI provider is configured", outcome["error"])
        with patch.dict(os.environ, KEYS), patch("streamlit.secrets", {}), \
             patch("modules.processor.urlopen", return_value=gemini_reply(variations(12))):
            outcome = gencite.generate_variations("crm software")
        self.assertTrue(outcome["fallback"])
        self.assertIn("invalid query variations", outcome["error"])

    def test_model_overrides_and_missing_model_hint(self):
        with patch.dict(os.environ, {**KEYS, "GENCITE_GEMINI_MODEL": "gemini-x", "GENCITE_OPENAI_MODEL": "gpt-y"}), \
             patch("streamlit.secrets", {}):
            self.assertEqual(gencite.provider_model("gemini"), "gemini-x")
            self.assertEqual(gencite.provider_model("openai"), "gpt-y")
            message = gencite._safe_error(AIError("Gemini HTTP 404: unavailable", 404), "key", "gemini")
            self.assertIn("GENCITE_GEMINI_MODEL=gemini-x", message)
        with patch.dict(os.environ, {**KEYS, "GENCITE_GEMINI_MODEL": "auto"}), patch("streamlit.secrets", {}), \
             patch("modules.gencite.resolve_model", return_value="models/gemini-auto") as resolve:
            self.assertEqual(gencite._resolved_model("gemini", "key"), "models/gemini-auto")
            resolve.assert_called_once_with("key", "auto")


class VariationValidationTests(unittest.TestCase):
    def test_duplicates_and_invalid_items_are_dropped_but_too_few_are_rejected(self):
        items = variations()["variations"]
        noisy = items + [items[0] | {"text": "QUERY 0"}, {"text": "x", "tag": "Unknown"}, {"text": "", "tag": "Pricing"}]
        self.assertEqual(gencite.validate_variations({"variations": noisy}), items)
        self.assertIsNone(gencite.validate_variations({"variations": items[:39]}))
        self.assertIsNone(gencite.validate_variations({"variations": "bad"}))

    def test_local_templates_cover_every_intent(self):
        local = gencite.local_variations("crm")
        self.assertEqual(len(local), 50)
        self.assertEqual({v["tag"] for v in local}, set(gencite.ALLOWED_TAGS))
        self.assertIsNotNone(gencite.validate_variations(local))


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, KEYS)
        patcher.start()
        self.addCleanup(patcher.stop)
        secrets = patch("streamlit.secrets", {})
        secrets.start()
        self.addCleanup(secrets.stop)
        self.queries = [f"query {i}" for i in range(12)]

    def test_providers_run_side_by_side_in_bounded_batches(self):
        fake = FakeProviders()
        with patch("modules.gencite._call_json", side_effect=fake):
            result = gencite.run_analysis("Acme", self.queries, competitors="Beta, Beta", aliases=["ACM Labs"])
        self.assertEqual(sorted(len(numbered_queries(c.prompt)) for c in fake.calls), [2, 2, 10, 10])
        self.assertEqual({c.key for c in fake.calls if c.provider == "openai"}, {"private-openai"})
        self.assertTrue(all(c.options["temperature"] == 0.9 for c in fake.calls))
        self.assertEqual([q["text"] for q in result["queries"]], self.queries)
        first = result["queries"][0]
        self.assertEqual([(m["name"], m["count"], m["runs"]) for m in first["models"]],
                         [("Gemini", 2, 5), ("ChatGPT", 5, 5)])
        self.assertEqual(first["competitors"], [{"name": "Beta", "count": 2, "runs": 10}])
        self.assertEqual((result["totalCitations"], result["totalRuns"], result["overallPct"]), (84, 120, 70))
        self.assertEqual(result["model"], "gemini-2.5-flash + gpt-4.1-mini")
        self.assertEqual(result["providerErrors"], [])
        self.assertEqual(result["context"], {"aliases": ["ACM Labs"], "competitors": ["Beta"]})

    def test_failed_provider_or_batch_is_reported_without_discarding_other_answers(self):
        fake = FakeProviders(fail={"openai": lambda queries: True, "gemini": lambda queries: len(queries) == 2})
        with patch("modules.gencite._call_json", side_effect=fake):
            result = gencite.run_analysis("Acme", self.queries)
        self.assertEqual(len(result["queries"]), 10)
        self.assertEqual({m["name"] for q in result["queries"] for m in q["models"]}, {"Gemini"})
        messages = [error["message"] for error in result["providerErrors"]]
        self.assertTrue(messages[0].startswith("Gemini answered 10 of 12 queries"))
        self.assertTrue(messages[1].startswith("ChatGPT did not respond"))
        self.assertNotIn("private-openai", " ".join(messages))

    def test_unconfigured_or_failed_providers_raise_only_when_nothing_succeeds(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("modules.gencite._call_json", side_effect=FakeProviders()):
            result = gencite.run_analysis("Acme", self.queries[:1])
        self.assertEqual(result["providerErrors"][0]["message"],
                         "ChatGPT did not respond: not configured. Contact the app administrator.")
        with patch("modules.gencite._call_json", side_effect=FakeProviders(fail={
                "gemini": lambda q: True, "openai": lambda q: True})):
            with self.assertRaisesRegex(gencite.GenCiteError, "Gemini: .*ChatGPT: "):
                gencite.run_analysis("Acme", self.queries[:1])
        with patch.dict(os.environ, {"GEMINI_KEY": "", "OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(gencite.GenCiteError, "not configured"):
                gencite.run_analysis("Acme", self.queries[:1])

    def test_rate_limits_are_retried_with_backoff(self):
        replies = [AIError("Quota", 429), {"results": [{"queryIndex": 0, "answers": ["Acme"] * 5}]}]
        with patch("modules.gencite._call_json", side_effect=replies), patch("modules.gencite.time.sleep") as sleep:
            result = gencite.run_analysis("Acme", ["q"], ["gemini"])
        self.assertEqual(result["overallPct"], 100)
        sleep.assert_called_once_with(2.0)

    def test_inputs_are_validated_before_any_request(self):
        with patch("modules.gencite._call_json") as call:
            for brand, queries in (("", ["q"]), ("x" * 121, ["q"]), ("Acme", []), ("Acme", ["q"] * 2),
                                   ("Acme", [f"q{i}" for i in range(51)]), ("Acme", ["x" * 301])):
                with self.subTest(brand=brand[:5], count=len(queries)), self.assertRaises(ValueError):
                    gencite.run_analysis(brand, queries)
            with self.assertRaises(ValueError):
                gencite.run_analysis("Acme", ["q"], ["claude"])
            call.assert_not_called()

    def test_answer_batches_must_cover_each_index_once(self):
        good = {"results": [{"queryIndex": 1, "answers": ["b"] * 5}, {"queryIndex": 0, "answers": ["a"] * 5}]}
        self.assertEqual(gencite.validate_analysis_batch(good, 2), [["a"] * 5, ["b"] * 5])
        for bad in ({"results": good["results"][:1]},
                    {"results": [good["results"][0], good["results"][0]]},
                    {"results": [{"queryIndex": 2, "answers": ["a"] * 5}, good["results"][1]]},
                    {"results": [{"queryIndex": True, "answers": ["a"] * 5}, good["results"][0]]},
                    {"results": [{"queryIndex": 0, "answers": ["a"] * 4}, good["results"][0]]},
                    {"results": [{"queryIndex": 0, "answers": [""] * 5}, good["results"][0]]}):
            with self.subTest(bad=bad):
                self.assertIsNone(gencite.validate_analysis_batch(bad, 2))


class AggregationTests(unittest.TestCase):
    def provider(self, name, queries, count):
        provider = "gemini" if name == "Gemini" else "openai"
        answers = [["Acme"] * count + ["x"] * (5 - count) for _ in queries]
        return gencite.build_provider_result(provider, name.lower(), "Acme", queries, answers, ["Beta"], [])

    def test_run_again_adds_samples_per_provider_and_keeps_unanswered_queries(self):
        first = gencite.merge_analysis_results(None, gencite.combine_provider_results(
            [self.provider("Gemini", ["a", "b"], 1), self.provider("ChatGPT", ["a", "b"], 5)]))
        self.assertEqual((first["runCount"], first["previousOverallPct"], first["overallPct"]), (1, None, 60))
        second = gencite.merge_analysis_results(first, gencite.combine_provider_results(
            [self.provider("Gemini", ["a"], 5)]))
        self.assertEqual((second["runCount"], second["previousOverallPct"]), (2, 60))
        a, b = second["queries"]
        self.assertEqual([(m["name"], m["count"], m["runs"], m["pct"]) for m in a["models"]],
                         [("Gemini", 6, 10, 60), ("ChatGPT", 5, 5, 100)])
        self.assertEqual(a["competitors"], [{"name": "Beta", "count": 0, "runs": 15}])
        self.assertEqual((b["cited"], b["total"]), (6, 10))
        self.assertEqual((second["totalCitations"], second["totalRuns"], second["overallPct"]), (17, 25, 68))
        self.assertEqual(gencite.delta_label(second), "+8 pts vs previous round")
        # Gemini 7/15 (47%) across both queries vs ChatGPT 10/10.
        self.assertEqual(gencite.model_leader_note(second), "ChatGPT mentions Acme 53 pts more often than Gemini.")
        self.assertEqual(gencite.query_chart_spec(second)["encoding"]["yOffset"]["sort"], ["Gemini", "ChatGPT"])


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, KEYS)
        patcher.start()
        self.addCleanup(patcher.stop)
        secrets = patch("streamlit.secrets", {})
        secrets.start()
        self.addCleanup(secrets.stop)
        gemini = gencite.build_provider_result("gemini", "g", "Acme", ["crm pricing", "crm reviews"],
                                               [["Acme"] + ["Beta"] * 4, ["x"] * 5], ["Beta"], [])
        openai = gencite.build_provider_result("openai", "o", "Acme", ["crm pricing", "crm reviews"],
                                               [["Acme"] * 3 + ["x"] * 2, ["Beta"] * 5], ["Beta"], [])
        self.result = gencite.merge_analysis_results(None, gencite.combine_provider_results([gemini, openai]))
        self.result["context"] = {"competitors": ["Beta"], "market": "CRM for agencies", "region": ""}

    def test_recommendations_use_measured_gaps_and_gencite_scoring(self):
        fake = FakeProviders()
        with patch("modules.gencite._call_json", side_effect=fake):
            outcome = gencite.generate_recommendations(self.result)
        call = fake.calls[0]
        self.assertEqual(call.provider, "gemini")
        self.assertIn('0: query="crm pricing" brandMentionRate=40% competitorMentionRate=40%', call.prompt)
        self.assertIn('1: query="crm reviews" brandMentionRate=0% competitorMentionRate=50%', call.prompt)
        self.assertIn('The brand operates in: "CRM for agencies".', call.prompt)
        self.assertEqual(call.schema["properties"]["recommendations"]["items"]["properties"]
                         ["sourceQueryIndexes"]["items"]["maximum"], 1)
        self.assertEqual(len(outcome["recommendations"]), 5)
        rec = recommendation(0, indexes=(1,))
        score = gencite.score_recommendation(rec, self.result)
        # gap 50, consistency 100 (both 0%), relevance 80, freshness 50 -> (17.5+20+20+5)/0.9
        self.assertEqual((score["score"], score["used"]), (69, [1]))
        self.assertEqual(gencite.gap_line([1], self.result),
                         "Competitors appeared in 5 of 10 tested answers. Acme appeared in 0.")
        self.assertEqual(gencite.evidence_rows([1, 9], self.result),
                         [{"query": "crm reviews", "stats": "Gemini 0/5 · ChatGPT 0/5 · Beta 5/10"}])

    def test_keywords_never_repeat_tested_queries_and_rank_by_novelty(self):
        suggestions = [keyword("CRM Pricing")] + [keyword(f"new idea {i}", indexes=(0,)) for i in range(7)]
        with patch("modules.gencite._call_json", side_effect=FakeProviders(keywords=suggestions)):
            outcome = gencite.generate_keywords(self.result)
        self.assertEqual([k["keyword"] for k in outcome["keywords"]], [f"new idea {i}" for i in range(7)])
        # Shares "crm" and "pricing" with a tested query: half its words are new.
        self.assertEqual(gencite.novelty("crm pricing for startups", self.result), 50)
        self.assertEqual(gencite.novelty("ราคา", self.result), 100)
        score = gencite.score_keyword(keyword("crm pricing for startups", coverage="weak"), self.result)
        self.assertEqual(score["score"], 72)  # 0.35*90 + 0.25*70 + 0.25*50 + 0.15*70
        with patch("modules.gencite._call_json", side_effect=FakeProviders(keywords=suggestions[:4])):
            with self.assertRaisesRegex(gencite.GenCiteError, "invalid keyword suggestions"):
                gencite.generate_keywords(self.result)

    def test_invalid_recommendations_are_dropped_or_rejected(self):
        bad = [recommendation(0, indexes=(5,)), recommendation(1) | {"confidence": "sure"},
               recommendation(2) | {"relevance": 101}, recommendation(3) | {"supportingTerms": ["a"]}]
        self.assertIsNone(gencite.validate_recommendations({"recommendations": bad + [recommendation(4)]}, 2))
        mixed = [recommendation(i) for i in range(3)] + bad
        self.assertEqual(len(gencite.validate_recommendations({"recommendations": mixed}, 2)), 3)


class MemoryRef:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def collection(self, name):
        return MemoryRef(self.db, f"{self.path}/{name}")

    document = collection

    def set(self, value, **kwargs):
        self.db.records[self.path] = json.loads(json.dumps(value))

    def update(self, value, **kwargs):
        self.db.records[self.path].update(value)

    def delete(self, **kwargs):
        self.db.records.pop(self.path, None)

    def stream(self, **kwargs):
        return [SimpleNamespace(to_dict=lambda v=value: dict(v)) for path, value in self.db.records.items()
                if path.rsplit("/", 1)[0] == self.path]


class MemoryDB:
    def __init__(self):
        self.records = {}

    def collection(self, name):
        return MemoryRef(self, name)


class StoreTests(unittest.TestCase):
    def test_saved_analyses_are_private_per_account_and_round_trip(self):
        db = MemoryDB()
        result = {"brand": "Acme", "overallPct": 40, "totalCitations": 4, "totalRuns": 10, "runCount": 2,
                  "previousOverallPct": 30, "model": "m", "context": {"competitors": ["Beta"]},
                  "queries": [{"text": "q", "cited": 4, "total": 10, "models": []}], "providerErrors": [{}]}
        record = store.save_analysis(db, "Editor@Example.com", "  Acme   launch ", result)
        self.assertEqual((record["name"], record["queryCount"], record["runCount"]), ("Acme launch", 1, 2))
        self.assertNotIn("providerErrors", record)
        owner = store.library_owner_id("editor@example.com")
        self.assertIn(f"sensemaker_gencite/{owner}/analyses/{record['id']}", db.records)
        self.assertEqual(store.list_analyses(db, "editor@example.com"), [record])
        self.assertEqual(store.list_analyses(db, "other@example.com"), [])
        store.rename_analysis(db, "editor@example.com", record["id"], "Renamed")
        self.assertEqual(store.list_analyses(db, "editor@example.com")[0]["name"], "Renamed")
        with self.assertRaises(ValueError):
            store.rename_analysis(db, "editor@example.com", record["id"], " ")
        store.delete_analysis(db, "editor@example.com", record["id"])
        self.assertEqual(store.list_analyses(db, "editor@example.com"), [])
        with self.assertRaises(ValueError):
            store.save_analysis(db, "editor@example.com", "Empty", {"queries": []})

    def test_published_marks_toggle(self):
        db = MemoryDB()
        store.set_published(db, "editor@example.com", "Pricing guide", True)
        self.assertEqual(list(store.load_published(db, "editor@example.com")), ["Pricing guide"])
        self.assertEqual(store.load_published(db, "other@example.com"), {})
        store.set_published(db, "editor@example.com", "Pricing guide", False)
        self.assertEqual(store.load_published(db, "editor@example.com"), {})


class GenCiteWorkspaceTests(unittest.TestCase):
    def run_app(self, db, fake):
        stack = [patch.dict(os.environ, KEYS), patch("modules.config.load_environment"), patch("streamlit.secrets", {}),
                 patch("modules.auth.require_access", return_value="editor@example.com"),
                 patch("modules.database.init_db", return_value=db), patch("modules.gencite._call_json", side_effect=fake)]
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py"), default_timeout=30).run()

    def assert_ok(self, app):
        self.assertFalse(list(app.exception), [e.value for e in app.exception])
        visible = str(app) + repr(app.session_state.filtered_state)
        self.assertNotIn("private-gemini", visible)
        self.assertNotIn("private-openai", visible)

    def test_generate_run_save_recommend_and_test_keywords(self):
        db, fake = MemoryDB(), FakeProviders()
        app = self.run_app(db, fake)
        self.assert_ok(app)
        self.assertTrue(app.button(key="gencite_run").disabled)
        app.text_input(key="gencite_query").input("crm software").run()
        app.button(key="gencite_generate_variations").click().run()
        self.assertEqual(len(app.session_state["gencite_rows"]), 50)
        app.button(key="gencite_clear_selection").click().run()
        self.assertTrue(app.button(key="gencite_run").disabled)
        app.button(key="gencite_select_all").click().run()
        app.text_input(key="gencite_brand").input("Acme").run()
        app.text_input(key="gencite_aliases").input("ACM Labs").run()
        app.text_input(key="gencite_competitors").input("Beta").run()
        self.assertEqual(app.button(key="gencite_run").label, "Run analysis on 50 queries")
        app.button(key="gencite_run").click().run()
        self.assert_ok(app)
        result = app.session_state["gencite_result"]
        self.assertEqual((result["totalRuns"], result["overallPct"], result["runCount"]), (500, 70, 1))
        self.assertIn("70%", [m.value for m in app.metric])

        app.button(key="gencite_run_again").click().run()
        self.assert_ok(app)
        self.assertEqual((app.session_state["gencite_result"]["runCount"],
                          app.session_state["gencite_result"]["totalRuns"]), (2, 1000))

        app.button(key="gencite_save").click().run()
        self.assert_ok(app)
        self.assertEqual(len(store.list_analyses(db, "editor@example.com")), 1)
        saved_names = app.dataframe[-1].value["Name"].tolist()
        self.assertEqual(len(saved_names), 1)
        self.assertTrue(saved_names[0].startswith("Acme — "))

        app.button(key="gencite_generate_recs").click().run()
        self.assert_ok(app)
        self.assertEqual(len(app.session_state["gencite_recs"]["recommendations"]), 5)
        app.button(key="gencite_publish_1").click().run()
        self.assertEqual(len(store.load_published(db, "editor@example.com")), 1)

        app.button(key="gencite_generate_keywords").click().run()
        app.button(key="gencite_queue_1").click().run()
        self.assertEqual(app.button(key="gencite_test_keywords").label, "Test 1 queued keyword")
        app.button(key="gencite_test_keywords").click().run()
        self.assert_ok(app)
        tested = app.session_state["gencite_result"]
        self.assertEqual((len(tested["queries"]), tested["runCount"]), (1, 1))
        self.assertTrue(any("queued keywords were tested" in s.value for s in app.success))

    def test_saved_analysis_opens_renames_and_deletes(self):
        db = MemoryDB()
        result = gencite.merge_analysis_results(None, gencite.build_provider_result(
            "gemini", "g", "Acme", ["q"], [["Acme"] * 5], [], ["ACM"]))
        result["context"] = {"aliases": ["ACM"], "competitors": []}
        record = store.save_analysis(db, "editor@example.com", "Launch", result)
        app = self.run_app(db, FakeProviders())
        self.assert_ok(app)
        app.button(key="gencite_open_saved").click().run()
        self.assert_ok(app)
        self.assertEqual(app.session_state["gencite_result"]["brand"], "Acme")
        self.assertEqual(app.session_state["gencite_aliases"], "ACM")
        self.assertTrue(any("Opened from saved analysis" in i.value for i in app.info))
        app.text_input(key=f"gencite_rename_{record['id']}").input("Relaunch").run()
        app.button(key="gencite_rename_saved").click().run()
        self.assertEqual(store.list_analyses(db, "editor@example.com")[0]["name"], "Relaunch")
        app.button(key="gencite_delete_saved").click().run()
        app.button(key="gencite_confirm_delete_btn").click().run()
        self.assert_ok(app)
        self.assertEqual(store.list_analyses(db, "editor@example.com"), [])

    def test_unconfigured_server_blocks_analysis_with_guidance(self):
        app = self.run_app(MemoryDB(), FakeProviders())
        os.environ.update({"GEMINI_KEY": "", "OPENAI_API_KEY": ""})
        app.run()
        self.assert_ok(app)
        self.assertTrue(app.button(key="gencite_run").disabled)
        self.assertTrue(any("No AI provider is configured" in e.value for e in app.error))


if __name__ == "__main__":
    unittest.main()
