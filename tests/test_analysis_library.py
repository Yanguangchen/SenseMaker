"""Current sources must not inherit the earlier research dataset or selections."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.analysis_library import select_history, select_records, source_key
from modules.reddit import CAMBODIA_URL
from modules.saved_links import library_owner_id

GROUP = "https://www.facebook.com/groups/current/"
OLD_GROUP = "https://www.facebook.com/groups/legacy/"


def post(key, source=GROUP, day=1, **extra):
    return {"_id": key, "url": f"{source.rstrip('/')}/posts/{key}", "target_url": source,
            "scraped_at": f"2026-10-{day:02d}T00:00:00+00:00", "raw_text": "Saved content", **extra}


class SelectionTests(unittest.TestCase):
    def test_legacy_records_do_not_fill_limit_before_current_sources_are_considered(self):
        rows = [post(str(i), OLD_GROUP) for i in range(132)] + [post("current", day=2)]
        self.assertEqual([r["_id"] for r in select_records(rows, source_urls=[GROUP], limit=1)], ["current"])

    def test_newest_collection_dates_win_and_missing_dates_are_last(self):
        rows = [post("old"), post("new", day=5), post("undated", scraped_at="", saved_at="")]
        self.assertEqual([r["_id"] for r in select_records(rows, source_urls=[GROUP], limit=2)], ["new", "old"])

    def test_matching_uses_source_urls_not_the_language_or_words_in_a_post(self):
        rows = [post("current", raw_text="A post in Bangla"),
                post("legacy", OLD_GROUP, raw_text="Thailand Cambodia boycott"),
                post("lookalike", "https://facebook.com.evil/groups/current/")]
        self.assertEqual([r["_id"] for r in select_records(rows, source_urls=[GROUP], limit=50)], ["current"])

    def test_mobile_group_urls_reddit_threads_and_saved_news_are_supported(self):
        rows = [post("facebook", target_url="", url="https://m.facebook.com/groups/current/posts/42/?ref=feed"),
                post("reddit", target_url="", url="https://old.reddit.com/r/cambodia/comments/a/title/"),
                post("news", "https://news.google.com/rss/articles/a", source_type="news_article")]
        self.assertEqual(len(select_records(rows, source_urls=[GROUP, CAMBODIA_URL], limit=50)), 3)
        self.assertNotEqual(source_key("https://reddit.com/r/cambodiafake/"), source_key(CAMBODIA_URL))

    def test_full_library_is_explicit_and_empty_sources_do_not_restore_legacy_posts(self):
        rows = [post("legacy", OLD_GROUP)]
        self.assertEqual(select_records(rows, source_urls=[], limit=50), [])
        self.assertEqual(select_records(rows, source_urls=None, limit=50), rows)

    def test_loader_filters_before_limit_and_uses_authoritative_document_ids(self):
        from dashboard import _fetch_firestore_posts
        db = MagicMock()
        docs = [SimpleNamespace(id=str(i), to_dict=lambda: post("stored", OLD_GROUP)) for i in range(50)]
        docs.append(SimpleNamespace(id="actual", to_dict=lambda: post("wrong", day=5)))
        db.collection.return_value.where.return_value.stream.return_value = docs
        with patch("dashboard.init_db", return_value=db):
            rows = _fetch_firestore_posts("processed", 1, [GROUP])
        self.assertEqual([r["_id"] for r in rows], ["actual"])
        db.collection.return_value.where.assert_called_once_with("status", "==", "processed")
        db.collection.return_value.limit.assert_not_called()

    def test_old_or_mixed_run_summaries_cannot_contaminate_current_source_trends(self):
        rows = [{"run_id": "old"}, {"run_id": "current", "record_sources": [post("a")]},
                {"run_id": "mixed", "record_sources": [post("a"), post("b", OLD_GROUP)]}]
        self.assertEqual([r["run_id"] for r in select_history(rows, [GROUP])], ["current"])
        self.assertEqual(select_history(rows, None), rows)

    def test_saved_run_records_the_sources_of_successfully_analyzed_posts(self):
        from modules.sentiment_history import persist_analysis_run
        db = MagicMock()
        row = post("a")
        snapshot = persist_analysis_run(db, [row, post("failed", OLD_GROUP)],
                                        {"a": {"sentiment": "Joy"}}, provider="gemini", targets=[])
        self.assertEqual(snapshot["record_sources"], [{"url": row["url"], "target_url": GROUP, "source_type": ""}])


class ScopeUITests(unittest.TestCase):
    script = "from dashboard import _render_analysis_sources\n_render_analysis_sources('owner@example.com')\n"
    library_key = f"saved_links_{library_owner_id('owner@example.com')}_facebook"

    def app(self, error=False):
        app = AppTest.from_string(self.script)
        app.session_state[self.library_key] = {"urls": [GROUP], "error": error}
        return app

    def seed_stale_data(self, app):
        app.session_state["firestore_rows"] = [post("legacy", OLD_GROUP)]
        app.session_state["processed_rows"] = [post("legacy", OLD_GROUP)]
        app.session_state["analysis_last_results"] = {"analyses": [{"_id": "legacy"}]}
        app.session_state["analysis_record_selection"] = ["legacy"]
        app.session_state["processed_delete_pending"] = ["legacy"]

    def assert_cleared(self, app):
        self.assertFalse(list(app.exception))
        for key in ("firestore_rows", "processed_rows", "analysis_last_results",
                    "analysis_record_selection", "processed_delete_pending"):
            self.assertNotIn(key, app.session_state)

    def test_existing_sessions_and_source_switches_clear_stale_results(self):
        app = self.app()
        self.seed_stale_data(app)
        app.run()
        self.assertEqual(app.selectbox(key="analysis_source_scope").value, "Current sources")
        self.assert_cleared(app)
        self.seed_stale_data(app)
        app.selectbox(key="analysis_source_scope").select("All saved records (including older sources)").run()
        self.assert_cleared(app)
        self.seed_stale_data(app)
        app.selectbox(key="analysis_source_scope").select("Current sources").run()
        self.assert_cleared(app)

    def test_changing_saved_links_or_a_library_failure_never_keeps_loaded_records(self):
        app = self.app().run()
        self.seed_stale_data(app)
        app.session_state[self.library_key] = {"urls": [GROUP + "another"], "error": False}
        app.run()
        self.assert_cleared(app)
        self.seed_stale_data(app)
        app.session_state[self.library_key] = {"urls": [GROUP], "error": True}
        app.run()
        self.assert_cleared(app)
        self.assertTrue(any("could not be loaded" in w.value for w in app.warning))


if __name__ == "__main__":
    unittest.main()
