"""Analysis snapshots, atomic persistence, and the visible timeline contract."""

import copy
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import altair as alt
from streamlit.testing.v1 import AppTest

from modules.sentiment_history import build_snapshot, chart_spec, load_history, persist_analysis_run


def snapshot(sentiments, day=1, selected_count=None):
    return build_snapshot(
        [{"sentiment": value, "model": "test-model"} for value in sentiments],
        run_id=f"run-{day}", completed_at=f"2026-10-{day:02d}T12:00:00+00:00",
        selected_count=selected_count if selected_count is not None else len(sentiments),
        provider="gemini", targets=["example"],
    )


class MemoryDatabase:
    def __init__(self):
        self.documents = {}
        self.fail_commit = False

    def collection(self, name):
        def stream(**kwargs):
            values = [copy.deepcopy(value) for (collection, key), value in self.documents.items() if collection == name]
            return [SimpleNamespace(to_dict=lambda value=value: value)
                    for value in sorted(values, key=lambda value: value["completed_at"])]
        collection = SimpleNamespace(document=lambda key: (name, key))
        collection.order_by = lambda key: SimpleNamespace(stream=stream)
        return collection

    def batch(self):
        writes = []

        def commit(**kwargs):
            if self.fail_commit:
                raise RuntimeError("offline")
            for ref, value, merge in writes:
                self.documents[ref] = {**(self.documents.get(ref, {}) if merge else {}), **copy.deepcopy(value)}
        return SimpleNamespace(set=lambda ref, value, merge=False: writes.append((ref, value, merge)), commit=commit)


class SnapshotTests(unittest.TestCase):
    def test_polarity_and_partial_run_counts_exclude_failures(self):
        row = snapshot(["Joy", "Neutral", "Anger", "Anxiety"], selected_count=6)
        self.assertEqual(row["score"], -25)
        self.assertEqual(row["processed_count"], 4)
        self.assertEqual(row["error_count"], 2)
        self.assertEqual(snapshot(["Joy"])["score"], 100)
        self.assertEqual(snapshot(["Anxiety", "Anger"])["score"], -100)
        self.assertEqual(snapshot(["Neutral"])["score"], 0)

    def test_no_results_or_unknown_labels_do_not_create_false_neutral_points(self):
        self.assertIsNone(snapshot([]))
        self.assertIsNone(snapshot(["unknown"]))

    def test_average_weights_runs_equally_and_chart_keeps_the_fixed_scale(self):
        rows = [snapshot(["Joy"]), snapshot(["Anger"] * 10, day=2)]
        spec = chart_spec(rows)
        self.assertEqual(spec["layer"][2]["data"]["values"][0]["average"], 0)
        self.assertEqual(spec["layer"][1]["encoding"]["y"]["scale"]["domain"], [-100, 100])
        self.assertEqual(spec["layer"][1]["encoding"]["x"]["field"], "completed_at")
        self.assertTrue(spec["layer"][2]["mark"]["strokeDash"])
        alt.LayerChart.from_dict(spec).to_dict(validate=True)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.config = patch("modules.sentiment_history._get_collection_name", return_value="test_posts")
        self.config.start()
        self.addCleanup(self.config.stop)
        self.db = MemoryDatabase()

    def save(self, values):
        return persist_analysis_run(self.db, [{"_id": key} for key in values],
                                    {key: {"sentiment": value} for key, value in values.items() if value},
                                    provider="gemini", targets=[])

    def test_two_analyses_of_the_same_post_keep_both_snapshots_after_reload(self):
        first = self.save({"a": "Joy"})
        second = self.save({"a": "Anger"})
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual([row["score"] for row in load_history(self.db)], [100, -100])
        self.assertEqual(self.db.documents[("test_posts", "a")]["analysis"]["sentiment"], "Anger")
        self.db.documents.pop(("test_posts", "a"))
        self.assertEqual(len(load_history(self.db)), 2)

    def test_partial_run_commits_one_point_and_all_post_statuses(self):
        row = self.save({"a": "Joy", "b": "Neutral", "c": None})
        self.assertEqual(row["score"], 50)
        self.assertEqual(row["error_count"], 1)
        self.assertEqual(len(load_history(self.db)), 1)
        self.assertEqual(self.db.documents[("test_posts", "c")]["status"], "error")
        self.assertEqual(self.db.documents[("test_posts", "a")]["processed_at"], row["completed_at"])

    def test_commit_failure_cannot_leave_updated_posts_without_their_timeline_point(self):
        self.db.fail_commit = True
        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.save({"a": "Joy"})
        self.assertEqual(self.db.documents, {})

    def test_entirely_failed_run_does_not_change_the_timeline_average(self):
        self.save({"a": "Joy"})
        self.assertIsNone(self.save({"b": None}))
        self.assertEqual([row["score"] for row in load_history(self.db)], [100])


class TimelineUITests(unittest.TestCase):
    script = "from dashboard import _render_sentiment_history\n_render_sentiment_history()\n"

    def test_empty_and_unavailable_history_are_distinct(self):
        with patch("dashboard._load_sentiment_history", return_value=[]):
            app = AppTest.from_string(self.script).run()
            self.assertIn("Complete an analysis", app.info[0].value)
        with patch("dashboard._load_sentiment_history", side_effect=RuntimeError("offline")):
            app = AppTest.from_string(self.script).run()
            self.assertIn("could not be loaded", app.warning[0].value)
            self.assertEqual(len(app.info), 0)

    def test_single_point_and_trend_have_visible_values_and_accessible_table(self):
        rows = [snapshot(["Joy"]), snapshot(["Neutral"], day=2)]
        for count in (1, 2):
            with self.subTest(count=count), patch("dashboard._load_sentiment_history", return_value=rows[:count]):
                app = AppTest.from_string(self.script).run()
                self.assertFalse(list(app.exception))
                self.assertEqual(app.metric[2].value, str(count))
                self.assertEqual(len(app.get("arrow_vega_lite_chart")), 1)
                self.assertEqual(len(app.dataframe[0].value), count)
                if count == 2:
                    self.assertEqual(app.metric[0].delta, "-100.0 vs previous run")
                    self.assertEqual(app.metric[1].value, "+50.0")

    def test_dashboard_saves_one_run_and_invalidates_history_after_analysis(self):
        from dashboard import _process_selected_firestore_posts
        db = MemoryDatabase()
        with patch("dashboard.init_db", return_value=db), patch("dashboard.st.session_state", {}), \
                patch("dashboard.analyze_posts", return_value={"a": {"sentiment": "Joy"}}), \
                patch("dashboard._load_sentiment_history") as cached:
            result = _process_selected_firestore_posts([{"_id": "a", "raw_text": "Example"}])
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["history_run"]["score"], 100)
        cached.clear.assert_called_once()
        self.assertEqual(len(load_history(db)), 1)

    def test_dashboard_model_failure_creates_no_point(self):
        from dashboard import _process_selected_firestore_posts
        with patch("dashboard.init_db"), patch("dashboard.st.session_state", {}), \
                patch("dashboard.analyze_posts", side_effect=ValueError("invalid response")), \
                patch("dashboard.persist_analysis_run") as save:
            with self.assertRaises(ValueError):
                _process_selected_firestore_posts([{"_id": "a"}])
            save.assert_not_called()

    def test_each_dashboard_run_updates_the_chart_and_survives_a_new_session(self):
        import dashboard
        db = MemoryDatabase()
        dashboard._load_sentiment_history.clear()
        self.addCleanup(dashboard._load_sentiment_history.clear)
        path = str(Path(__file__).resolve().parents[1] / "dashboard.py")
        with patch.dict(os.environ, {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key",
                                     "PLAYWRIGHT_STORAGE_STATE": "/tmp/sensemaker-no-session-for-chart-test.json"}), \
                patch("modules.config.load_environment"), \
                patch("modules.auth.require_access", return_value="editor@example.com"), \
                patch("modules.database.init_db", return_value=db), \
                patch("modules.processor.analyze_posts", side_effect=[
                    {"a": {"sentiment": "Anger"}}, {"a": {"sentiment": "Joy"}},
                ]):
            app = AppTest.from_file(path)
            app.session_state["firestore_rows"] = [{"_id": "a", "raw_text": "Example"}]
            app.run()
            for expected_count in (1, 2):
                app.button(key="run_analysis_selected_btn").click().run()
                self.assertFalse(list(app.exception))
                metrics = {metric.label: metric.value for metric in app.metric}
                self.assertEqual(metrics["Analysis runs"], str(expected_count))
            self.assertEqual(metrics["Average sentiment"], "+0.0")
            trends = next(tab for tab in app.tabs if tab.label == "Sentiment Trends")
            analysis = next(tab for tab in app.tabs if tab.label == "Sentiment Analysis")
            self.assertEqual(len(trends.get("arrow_vega_lite_chart")), 1)
            self.assertEqual(len(analysis.get("arrow_vega_lite_chart")), 0)
            restored = AppTest.from_file(path).run()
            self.assertFalse(list(restored.exception))
            self.assertEqual({metric.label: metric.value for metric in restored.metric}["Analysis runs"], "2")


if __name__ == "__main__":
    unittest.main()
