"""Shared timeline reset, data preservation, and the confirmation flow."""

import copy
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules.google_news import build_scan_snapshot
from modules.sentiment_history import build_snapshot, persist_analysis_run, reset_timeline


class TimelineDatabase:
    def __init__(self):
        self.documents = {}
        self.commits = 0
        self.fail_on_commit = None
        self.fail_read = None
        self.before_commit = lambda: None

    def collection(self, name):
        def stream(order=None, **kwargs):
            if name == self.fail_read:
                raise RuntimeError("offline")
            rows = [(key, copy.deepcopy(row)) for (collection, key), row in self.documents.items()
                    if collection == name]
            if order:
                rows.sort(key=lambda item: item[1][order])
            return [SimpleNamespace(reference=(name, key), to_dict=lambda row=row: row)
                    for key, row in rows]

        return SimpleNamespace(
            document=lambda key: (name, key),
            select=lambda fields: SimpleNamespace(stream=stream),
            order_by=lambda field: SimpleNamespace(stream=lambda **kwargs: stream(field, **kwargs)),
        )

    def batch(self):
        writes = []

        def commit(**kwargs):
            self.commits += 1
            if len(writes) > 500:
                raise RuntimeError("batch too large")
            if self.commits == self.fail_on_commit:
                raise RuntimeError("offline")
            self.before_commit()
            for action, ref, value in writes:
                if action == "delete":
                    self.documents.pop(ref, None)
                elif action == "update":
                    self.documents[ref].update(copy.deepcopy(value))
                else:
                    self.documents[ref] = copy.deepcopy(value)

        return SimpleNamespace(
            delete=lambda ref: writes.append(("delete", ref, None)),
            set=lambda ref, value: writes.append(("set", ref, value)),
            update=lambda ref, value: writes.append(("update", ref, value)),
            commit=commit,
        )


class TimelineResetFixture(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in ("sentiment_history", "news_risk"):
            self.stack.enter_context(patch(f"modules.{module}._get_collection_name", return_value="test_posts"))
        self.db = TimelineDatabase()
        self.kept = {
            ("test_posts", "post"): {"status": "processed", "analysis": {"sentiment": "Joy"}},
            ("other_posts_analysis_runs", "run"): {"score": -100},
            ("other_posts_news_scans", "scan"): {"risk_score": 10},
        }
        self.db.documents.update(copy.deepcopy(self.kept))

    def seed_history(self, count=1):
        for index in range(count):
            self.db.documents[("test_posts_analysis_runs", f"run-{index}")] = build_snapshot(
                [{"sentiment": "Joy"}], run_id=f"run-{index}", completed_at="2026-10-01T00:00:00+00:00",
                selected_count=1, provider="gemini", targets=[],
            )
        self.db.documents[("test_posts_news_scans", "scan")] = build_scan_snapshot(
            [{"source_type": "news_article", "raw_text": "Thailand and Cambodia agree to peace talks"}],
            searches={"border_tension": "Thailand Cambodia"}, window="7d", editions=["en"],
            scanned_at="2026-10-02T00:00:00+00:00", scan_id="scan",
        )


class TimelineResetTests(TimelineResetFixture):
    def test_reset_large_history_preserves_records_analyses_and_other_libraries(self):
        self.seed_history(805)
        self.assertEqual(reset_timeline(self.db), {"analysis_runs": 805, "news_scans": 1})
        self.assertEqual(self.db.documents, self.kept)

    def test_empty_reset_is_safe_to_repeat(self):
        for _ in range(2):
            self.assertEqual(reset_timeline(self.db), {"analysis_runs": 0, "news_scans": 0})
        self.assertEqual(self.db.documents, self.kept)
        self.assertEqual(self.db.commits, 0)

    def test_read_failure_cannot_clear_either_history(self):
        self.seed_history()
        original = copy.deepcopy(self.db.documents)
        self.db.fail_read = "test_posts_news_scans"
        with self.assertRaises(RuntimeError):
            reset_timeline(self.db)
        self.assertEqual(self.db.documents, original)

    def test_new_runs_after_enumeration_are_kept(self):
        self.seed_history()
        self.db.before_commit = lambda: self.db.documents.update({
            ("test_posts_analysis_runs", "new-run"): {"score": 0},
            ("test_posts_news_scans", "new-scan"): {"risk_score": 0},
        })
        self.assertEqual(reset_timeline(self.db), {"analysis_runs": 1, "news_scans": 1})
        self.assertIn(("test_posts_analysis_runs", "new-run"), self.db.documents)
        self.assertIn(("test_posts_news_scans", "new-scan"), self.db.documents)

    def test_partial_reset_can_be_retried(self):
        self.seed_history(805)
        self.db.fail_on_commit = 2
        with self.assertRaises(RuntimeError):
            reset_timeline(self.db)
        self.assertTrue(all(self.db.documents[key] == row for key, row in self.kept.items()))
        self.db.fail_on_commit = None
        reset_timeline(self.db)
        self.assertEqual(self.db.documents, self.kept)


class TimelineResetUITests(TimelineResetFixture):
    script = "from dashboard import _render_sentiment_trends\n_render_sentiment_trends()"

    def setUp(self):
        super().setUp()
        import dashboard
        self.stack.enter_context(patch("dashboard.init_db", return_value=self.db))
        for loader in (dashboard._load_sentiment_history, dashboard._load_news_history):
            loader.clear()
            self.addCleanup(loader.clear)

    def app(self):
        app = AppTest.from_string(self.script, default_timeout=30).run()
        self.assertFalse(list(app.exception))
        return app

    def test_settings_requires_confirmation_and_cancel_preserves_history(self):
        self.seed_history()
        original = copy.deepcopy(self.db.documents)
        app = self.app()
        self.assertEqual([tab.label for tab in app.tabs],
                         ["Sentiment over time", "Border tension risk over time", "Settings"])
        settings = app.tabs[2]
        self.assertIn("reset_sentiment_timeline", [button.key for button in settings.button])
        self.assertNotIn("confirm_reset_sentiment_timeline", [button.key for button in app.button])
        app.button(key="reset_sentiment_timeline").click().run()
        self.assertIn("cannot be undone", app.warning[-1].value)
        self.assertEqual(self.db.documents, original)
        app.button(key="cancel_reset_sentiment_timeline").click().run()
        self.assertFalse(list(app.exception))
        self.assertNotIn("confirm_reset_sentiment_timeline", [button.key for button in app.button])
        self.assertEqual(self.db.documents, original)
        self.assertEqual(self.db.commits, 0)

    def test_confirm_refreshes_both_timelines_and_next_analysis_starts_fresh(self):
        self.seed_history()
        app = self.app()
        self.assertIn("Analysis runs", [metric.label for metric in app.metric])
        self.assertIn("News scans", [metric.label for metric in app.metric])
        app.button(key="reset_sentiment_timeline").click().run()
        app.button(key="confirm_reset_sentiment_timeline").click().run()
        self.assertFalse(list(app.exception))
        self.assertIn("Removed 1 analysis run(s) and 1 news scan(s)", app.success[0].value)
        self.assertEqual(len(app.metric), 0)
        self.assertEqual(len(app.dataframe), 0)
        self.assertEqual(self.db.documents, self.kept)
        self.assertEqual(len(self.app().metric), 0)

        persist_analysis_run(self.db, [{"_id": "post"}], {"post": {"sentiment": "Anger"}},
                             provider="gemini", targets=[])
        app.button(key="refresh_sentiment_history").click().run()
        self.assertFalse(list(app.exception))
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics["Analysis runs"], "1")
        self.assertEqual(metrics["Latest sentiment"], "-100.0")
        self.assertEqual(metrics["News searches"], "0")

    def test_confirmation_cannot_reset_history_after_access_is_lost(self):
        self.seed_history()
        original = copy.deepcopy(self.db.documents)
        with patch("dashboard.require_access", return_value="editor@example.com") as access:
            app = AppTest.from_string(
                "from dashboard import require_access, _render_sentiment_trends\n"
                "if require_access() is not None:\n    _render_sentiment_trends()",
                default_timeout=30,
            ).run()
            app.button(key="reset_sentiment_timeline").click().run()
            access.return_value = None
            app.button(key="confirm_reset_sentiment_timeline").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(self.db.documents, original)
        self.assertEqual(self.db.commits, 0)

    def test_failure_shows_error_and_refreshes_partially_deleted_history(self):
        self.seed_history(401)
        self.db.fail_on_commit = 2
        app = self.app()
        app.button(key="reset_sentiment_timeline").click().run()
        app.button(key="confirm_reset_sentiment_timeline").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(len(app.success), 0)
        self.assertIn("Some history may already have been removed", app.error[0].value)
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics["Analysis runs"], "1")
        self.assertEqual(metrics["News scans"], "1")
        self.assertNotIn("confirm_reset_sentiment_timeline", [button.key for button in app.button])


if __name__ == "__main__":
    unittest.main()
