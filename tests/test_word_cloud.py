"""The cloud remains available without negative posts and uses real text counts."""

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules.word_cloud_ui import build_cloud, content_words


def record(key, sentiment, text, **extra):
    return {"_id": key, "url": f"https://example.com/{key}",
            "analysis": {"sentiment": sentiment, "translation": text}, **extra}


class WordCloudTests(unittest.TestCase):
    def setUp(self):
        self.rows = [record("p", "Joy", "Peace peace brings hope."),
                     record("n", "Neutral", "Peace talks continue.")]

    def test_positive_and_neutral_only_have_actual_counts_and_source_examples(self):
        cloud = build_cloud(self.rows)
        terms = {term["word"]: term for term in cloud["terms"]}
        self.assertEqual((cloud["records"], cloud["tokens"]), (2, 7))
        self.assertEqual((terms["peace"]["count"], terms["peace"]["records"]), (3, 2))
        self.assertEqual({source["sentiment"] for source in terms["peace"]["examples"]}, {"Positive", "Neutral"})
        self.assertEqual(terms["peace"]["examples"][0]["url"], "https://example.com/p")

    def test_filters_include_both_negative_emotions_and_flattened_results(self):
        rows = self.rows + [{"sentiment": "Anger", "translation": "Border closure"},
                            {"sentiment": "Anxiety", "translation": "Border concern"}]
        for sentiment, count in [("All sentiments", 4), ("Positive", 1), ("Neutral", 1), ("Negative", 2)]:
            with self.subTest(sentiment=sentiment):
                self.assertEqual(build_cloud(rows, sentiment=sentiment)["records"], count)
        self.assertEqual(build_cloud(self.rows, sentiment="Negative")["terms"], [])

    def test_original_text_and_translation_fallback_do_not_count_summaries_or_topics(self):
        rows = [record("p", "Joy", "Peace", raw_text="សន្តិភាព\nLike\nComment\nShare"),
                {"_id": "n", "sentiment": "Neutral", "raw_text": "Trade\n120 comments",
                 "title": "duplicate title", "topics": ["fabricated"], "monitor": {"summary": "invented"}},
                {"_id": "empty", "sentiment": "Joy"}]
        translated = build_cloud(rows)
        self.assertEqual((translated["records"], translated["with_text"], translated["fallback"]), (3, 2, 1))
        self.assertEqual({term["word"] for term in translated["terms"]}, {"peace", "trade"})
        self.assertEqual({term["word"] for term in build_cloud(rows, original=True)["terms"]}, {"សន្តិភាព", "trade"})

    def test_duplicate_excluded_removed_and_error_records_cannot_inflate_counts(self):
        rows = self.rows + [self.rows[0], record("x", "Joy", "noise", analysis_excluded=True),
                            record("y", "Joy", "noise", content_removed=True),
                            record("z", "Neutral", "noise", source_type="batch_error")]
        self.assertEqual(build_cloud(rows), build_cloud(self.rows))

    def test_word_normalization_limits_hidden_terms_and_safe_links(self):
        self.assertEqual(content_words("Don't LIKE the CAFÉ café. https://example.com/secret <b>hope</b>"),
                         ["café", "café", "hope"])
        cloud = build_cloud(self.rows, excluded="peace, talks", limit=2)
        self.assertEqual([term["word"] for term in cloud["terms"]], ["brings", "continue"])
        for url in ["javascript:alert(1)", "https://user:password@example.com", "https://[invalid"]:
            cloud = build_cloud([record("x", "Joy", "hope", url=url)])
            self.assertEqual(cloud["terms"][0]["examples"][0]["url"], "")

    def test_examples_are_bounded_without_changing_occurrence_or_record_counts(self):
        cloud = build_cloud([record(str(i), "Neutral", "peace " * 1000) for i in range(8)])
        term = cloud["terms"][0]
        self.assertEqual((term["count"], term["records"], len(term["examples"])), (8000, 8, 4))
        self.assertLessEqual(len(term["examples"][0]["text"]), 422)

    def test_report_tabs_have_independent_clouds_with_no_negative_reports(self):
        script = ("from dashboard import _render_client_report\n"
                  f"rows = {self.rows!r}\n"
                  "_render_client_report(rows, 'latest_report', '', lambda: None)\n"
                  "_render_client_report(rows, 'saved_report', '', lambda: None)\n")
        with patch("modules.word_cloud_ui.components.declare_component") as declare:
            app = AppTest.from_string(script, default_timeout=30).run()
            self.assertFalse(list(app.exception))
            self.assertEqual([tab.label for tab in app.tabs].count("Word cloud"), 2)
            self.assertEqual([tab.label for tab in app.tabs].count("Negative Post Reports (0)"), 2)
            self.assertEqual({call.kwargs["key"] for call in declare.return_value.call_args_list},
                             {"latest_report_floating_cloud", "saved_report_floating_cloud"})
            app.selectbox(key="latest_report_cloud_sentiment").select("Positive").run()
            self.assertFalse(list(app.exception))
            self.assertEqual(declare.return_value.call_args_list[-2].kwargs["cloud"]["records"], 1)
            self.assertEqual(declare.return_value.call_args_list[-1].kwargs["cloud"]["records"], 2)
            app.selectbox(key="latest_report_cloud_sentiment").select("Negative").run()
            self.assertFalse(list(app.exception))
            self.assertTrue(any("No words match" in info.value for info in app.info))


if __name__ == "__main__":
    unittest.main()
