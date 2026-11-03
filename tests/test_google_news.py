"""Google News collection, keyword risk flags, the risk timeline and its dashboard views."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import altair as alt
from streamlit.testing.v1 import AppTest

from modules.database import upsert_post
from modules.google_news import (
    NewsError, assess_article, browse_url, build_scan_snapshot, collect_news, daily_flag_counts, parse_feed,
    search_jobs, search_url,
)
from modules.news_risk import daily_chart_spec, load_news_history, persist_news_scan, risk_chart_spec
from modules.sentiment_history import build_snapshot, chart_spec


def item(title, publisher="Khmer Times", link="https://news.google.com/rss/articles/CBMiA1?oc=5",
         published="Mon, 26 Oct 2026 04:00:00 GMT", source_url="https://www.khmertimeskh.com"):
    return (f"<item><title>{title} - {publisher}</title><link>{link}</link>"
            f"<pubDate>{published}</pubDate><description>&lt;a href=\"{link}\"&gt;{title}&lt;/a&gt;&amp;nbsp;"
            f"&lt;font color=\"#6f6f6f\"&gt;{publisher}&lt;/font&gt;</description>"
            f"<source url=\"{source_url}\">{publisher}</source></item>")


def feed(*items):
    return f"<?xml version=\"1.0\"?><rss version=\"2.0\"><channel><title>t</title>{''.join(items)}</channel></rss>".encode()


def article(title, day="2026-10-26", categories=("border_tension",)):
    return {"url": f"https://news.google.com/rss/articles/{abs(hash(title))}", "raw_text": title, "title": title,
            "source_type": "news_article", "created_at": f"{day}T04:00:00+00:00",
            "news_category": categories[0], "news_categories": list(categories),
            "news_assessment": assess_article(title)}


SEARCHES = {"oyoshi": "OYOSHI", "border_tension": "Thailand Cambodia border tension",
            "boycott": "boycott Thailand products Cambodia"}
KHMER_SEARCHES = {"border_tension": "ថៃ កម្ពុជា", "boycott": "ពហិការ ទំនិញថៃ"}


class FeedTests(unittest.TestCase):
    def test_search_urls_match_the_google_news_search_and_time_window(self):
        url = search_url("boycott Thailand products Cambodia", "7d", "th")
        query = parse_qs(urlsplit(url).query)
        self.assertTrue(url.startswith("https://news.google.com/rss/search?"))
        self.assertEqual(query["q"], ["boycott Thailand products Cambodia when:7d"])
        self.assertEqual((query["hl"], query["gl"], query["ceid"]), (["th"], ["TH"], ["TH:th"]))
        self.assertTrue(browse_url("OYOSHI").startswith("https://news.google.com/search?q=OYOSHI"))

    def test_items_become_news_records_with_clean_headlines_and_stable_links(self):
        rows = parse_feed(feed(item("Thai and Cambodian troops clash at border"),
                               item("No link", link="javascript:alert(1)"),
                               item("Cruise stranded", publisher="https://www.thetraveler.org/",
                                    link="https://news.google.com/rss/articles/CBMiB2?oc=5")),
                          "border_tension", "Thailand Cambodia border tension")
        self.assertEqual(len(rows), 2)
        first, second = rows
        self.assertEqual(first["title"], "Thai and Cambodian troops clash at border")
        self.assertEqual(first["raw_text"], first["title"])
        self.assertEqual(first["url"], "https://news.google.com/rss/articles/CBMiA1")
        self.assertEqual(first["created_at"], "2026-10-26T04:00:00+00:00")
        self.assertEqual((first["source_type"], first["platform"]), ("news_article", "google_news"))
        self.assertEqual(first["news_assessment"]["flags"], ["Escalation"])
        self.assertEqual(second["title"], "Cruise stranded")
        self.assertEqual(second["publisher"], "thetraveler.org")

    def test_unreadable_feed_is_a_news_error(self):
        with self.assertRaises(NewsError):
            parse_feed(b"<html>captcha</html", "oyoshi", "OYOSHI")


class AssessmentTests(unittest.TestCase):
    def test_boycott_and_tension_flags_need_a_thailand_or_cambodia_link(self):
        self.assertEqual(assess_article("Cambodians boycott Thai products amid row")["flags"][0], "Boycott")
        self.assertEqual(assess_article("Shoppers boycott a local brand")["flags"], [])
        self.assertEqual(assess_article("Simmering tensions in Botswana")["tension_level"], 0)

    def test_escalation_outranks_tension_and_talks_lower_it(self):
        self.assertEqual(assess_article("Thailand and Cambodia trade artillery fire, soldiers killed")["tension_level"], 2)
        self.assertEqual(assess_article("Thailand, Cambodia border dispute deepens")["tension_level"], 1)
        self.assertEqual(assess_article("Thailand, Cambodia agree ceasefire after clashes")["tension_level"], 1)
        breach = assess_article("Thailand accuses Cambodia of ceasefire violation")
        self.assertEqual(breach["tension_level"], 2)
        self.assertNotIn("De-escalation", breach["flags"])

    def test_short_words_do_not_match_inside_other_words(self):
        result = assess_article("Bangkok award for a warm, warding season")
        self.assertEqual(result["tone"], "Positive")
        self.assertNotIn("ban", result["matched_terms"])
        self.assertEqual(result["tension_level"], 0)

    def test_thai_and_khmer_headlines_are_scanned(self):
        self.assertIn("Escalation", assess_article("ทหารไทยปะทะกัมพูชาที่ชายแดน")["flags"])
        self.assertIn("Boycott", assess_article("ពហិការទំនិញថៃ នៅកម្ពុជា")["flags"])


class CollectionTests(unittest.TestCase):
    def test_articles_found_by_several_searches_are_kept_once_and_failed_searches_are_reported(self):
        shared = item("Cambodia boycotts Thai goods as border tension rises")

        def fetch(url):
            q = parse_qs(urlsplit(url).query)["q"][0]
            if q.startswith("OYOSHI"):
                raise NewsError("Google News is rate limiting requests.")
            return feed(shared) if "boycott" in q else feed(shared, item(
                "Thai troops on alert", link="https://news.google.com/rss/articles/CBMiC3"))

        with patch("modules.google_news.time.sleep"):
            rows = collect_news(SEARCHES, window="30d", editions=["en"], fetch=fetch)
        articles = [r for r in rows if r["source_type"] == "news_article"]
        warnings = [r for r in rows if r["source_type"] == "batch_warning"]
        self.assertEqual(len(articles), 2)
        shared_row = next(a for a in articles if "boycotts" in a["title"])
        self.assertEqual(shared_row["news_categories"], ["border_tension", "boycott"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("rate limiting", warnings[0]["raw_text"])
        self.assertIn("OYOSHI mentions", warnings[0]["target_url"])


    def test_khmer_searches_use_khmer_terms_and_skip_categories_without_them(self):
        self.assertEqual(search_jobs(SEARCHES, ["km"], KHMER_SEARCHES),
                         [("border_tension", "ថៃ កម្ពុជា", "km"), ("boycott", "ពហិការ ទំនិញថៃ", "km")])
        self.assertEqual(search_jobs(SEARCHES, ["km"]), [])
        queries = []

        def fetch(url):
            queries.append(parse_qs(urlsplit(url).query)["q"][0])
            return feed(item("ទាហានថៃ និងកម្ពុជា ប៉ះទង្គិចគ្នា"))

        rows = collect_news({"oyoshi": "OYOSHI"}, window="30d", editions=["km"],
                            khmer_searches={"border_tension": "ថៃ កម្ពុជា"}, fetch=fetch)
        self.assertEqual(queries, ["ថៃ កម្ពុជា when:30d"])
        self.assertEqual(rows[0]["news_edition"], "km")
        self.assertIn("Escalation", rows[0]["news_assessment"]["flags"])


class SnapshotTests(unittest.TestCase):
    def test_risk_counts_escalation_double_and_small_samples_cannot_spike(self):
        rows = [article("Thai and Cambodian soldiers killed in border clash"),
                article("Thailand, Cambodia border dispute deepens"),
                article("Cambodians boycott Thai snacks", categories=("boycott",)),
                article("OYOSHI opens new store", categories=("oyoshi",))]
        snap = build_scan_snapshot(rows, searches=SEARCHES, window="30d", editions=["en"])
        self.assertEqual(snap["risk_score"], 15.0)  # (2 + 1) / (2 × max(3 linked, 10))
        self.assertEqual(snap["risk_level"], "Low")
        self.assertEqual((snap["escalation_count"], snap["tension_count"], snap["boycott_count"]), (1, 1, 1))
        self.assertEqual(snap["category_counts"], {"oyoshi": 1, "border_tension": 2, "boycott": 1})
        self.assertIsNone(snap["risk_delta"])
        self.assertFalse(snap["rising"])

    def test_a_rise_of_ten_points_is_flagged_against_the_previous_scan(self):
        rows = [article(f"Thailand and Cambodia exchange artillery fire {i}") for i in range(10)]
        snap = build_scan_snapshot(rows, searches=SEARCHES, window="7d", editions=["en"],
                                   previous={"risk_score": 40.0})
        self.assertEqual((snap["risk_score"], snap["risk_level"], snap["risk_delta"]), (100.0, "High", 60.0))
        self.assertTrue(snap["rising"])
        self.assertIsNone(build_scan_snapshot([], searches=SEARCHES, window="7d", editions=["en"]))

    def test_daily_counts_exclude_de_escalation(self):
        rows = [article("Thai and Cambodian troops clash", day="2026-10-20"),
                article("Thailand and Cambodia agree to peace talks", day="2026-10-21")]
        self.assertEqual(daily_flag_counts(rows), [{"day": "2026-10-20", "flag": "Escalation", "articles": 1}])


class ChartTests(unittest.TestCase):
    def test_risk_and_daily_charts_are_valid_vega_lite(self):
        rows = [build_scan_snapshot([article("Thai Cambodia border dispute")], searches=SEARCHES, window="7d",
                                    editions=["en"], scan_id="a", scanned_at="2026-10-01T00:00:00+00:00"),
                build_scan_snapshot([article("Thai and Cambodian troops clash")] * 10, searches=SEARCHES,
                                    window="7d", editions=["en"], previous={"risk_score": 5.0},
                                    scan_id="b", scanned_at="2026-10-02T00:00:00+00:00")]
        spec = risk_chart_spec(rows)
        self.assertEqual(spec["layer"][1]["encoding"]["y"]["scale"]["domain"], [0, 100])
        self.assertEqual([p["scan_id"] for p in spec["layer"][4]["data"]["values"]], ["b"])
        alt.LayerChart.from_dict(spec).to_dict(validate=True)
        alt.Chart.from_dict(daily_chart_spec(daily_flag_counts([article("Thai troops clash")]))).to_dict(validate=True)

    def test_sentiment_runs_with_tension_or_boycott_signals_are_highlighted(self):
        def run(signal, day):
            return build_snapshot([{"sentiment": "Anger", "monitor": {"signal_type": signal,
                                                                     "thailand_relevance": "explicit"}}],
                                  run_id=str(day), completed_at=f"2026-10-0{day}T00:00:00+00:00",
                                  selected_count=1, provider="gemini", targets=[])
        rows = [run("none", 1), run("tension", 2), run("boycott_call", 3)]
        self.assertEqual(rows[1]["signal_counts"], {"tension": 1, "boycott_call": 0, "boycott_report": 0})
        spec = chart_spec(rows)
        self.assertEqual([p["run_id"] for p in spec["layer"][-1]["data"]["values"]], ["2", "3"])
        alt.LayerChart.from_dict(spec).to_dict(validate=True)
        legacy = {key: value for key, value in rows[0].items() if key != "signal_counts"}
        self.assertEqual(chart_spec([legacy])["layer"][-1]["data"]["values"], [])


class PersistenceTests(unittest.TestCase):
    def test_news_metadata_is_saved_with_the_article(self):
        db = MagicMock()
        doc = db.collection.return_value.document.return_value
        doc.get.return_value = SimpleNamespace(exists=False)
        record = parse_feed(feed(item("Cambodians boycott Thai products")), "boycott", "boycott")[0]
        upsert_post(db, record)
        saved = doc.set.call_args.args[0]
        self.assertEqual(saved["publisher"], "Khmer Times")
        self.assertEqual(saved["news_assessment"]["flags"], ["Boycott"])
        self.assertEqual(saved["status"], "pending")

    def test_scan_snapshots_round_trip_in_their_own_collection(self):
        store = {}
        db = MagicMock()
        db.collection.return_value.document.side_effect = lambda key: SimpleNamespace(
            set=lambda value, **kwargs: store.__setitem__(key, value))
        db.collection.return_value.order_by.return_value.stream.side_effect = lambda **kwargs: [
            SimpleNamespace(to_dict=lambda v=v: v) for v in sorted(store.values(), key=lambda v: v["scanned_at"])]
        with patch("modules.news_risk._get_collection_name", return_value="raw_posts"):
            persist_news_scan(db, {"scan_id": "x", "scanned_at": "2026-10-01T00:00:00+00:00", "risk_score": 1})
            persist_news_scan(db, {"scan_id": "x", "scanned_at": "2026-10-01T00:00:00+00:00", "risk_score": 1})
            self.assertEqual(len(load_news_history(db)), 1)
        db.collection.assert_called_with("raw_posts_news_scans")


class NewsUITests(unittest.TestCase):
    workspace = "from dashboard import _render_news_workspace\n_render_news_workspace()\n"
    history = "from dashboard import _render_news_risk_history\n_render_news_risk_history()\n"

    def test_search_shows_flags_alerts_and_saves_a_risk_point(self):
        rows = [article(f"Thai and Cambodian troops clash {i}") for i in range(6)] + [
            article("Cambodians boycott Thai products", categories=("boycott",)),
            {"url": "u", "source_type": "batch_warning", "target_url": "OYOSHI mentions · English",
             "raw_text": "Google News could not be reached."}]
        with patch("dashboard.collect_news", return_value=rows) as collect, \
                patch("dashboard._load_news_history", return_value=[{"risk_score": 10.0}]), \
                patch("dashboard.persist_news_scan") as persist, patch("dashboard.init_db"):
            app = AppTest.from_string(self.workspace).run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.dataframe), 0)
            app.button(key="collect_news_articles").click().run()
            self.assertFalse(list(app.exception))
        self.assertEqual(collect.call_args.args[0], SEARCHES)
        self.assertEqual(collect.call_args.kwargs["window"], "30d")
        self.assertEqual(collect.call_args.kwargs["editions"], ["en", "th", "km"])
        self.assertEqual(collect.call_args.kwargs["khmer_searches"], KHMER_SEARCHES)
        self.assertEqual(persist.call_args.args[1]["khmer_searches"], KHMER_SEARCHES)
        persist.assert_called_once()
        metrics = {m.label: m.value for m in app.metric}
        self.assertEqual(metrics["Articles found"], "7")
        self.assertEqual(metrics["Boycott flags"], "1")
        self.assertEqual(metrics["Border tension flags"], "6")
        self.assertEqual(metrics["Border tension risk"], "60 · High")
        self.assertIn("rising", app.error[0].value)
        self.assertTrue(any("boycott" in w.value for w in app.warning))
        self.assertTrue(any("could not be reached" in w.value for w in app.warning))
        self.assertEqual(len(app.get("arrow_vega_lite_chart")), 1)
        self.assertEqual(len(app.dataframe[0].value), 7)
        app.selectbox(key="news_show_filter").select("Boycott").run()
        self.assertEqual(len(app.dataframe[0].value), 1)

    def test_khmer_terms_are_only_shown_when_khmer_is_selected(self):
        with patch("dashboard.collect_news", return_value=[]) as collect, \
                patch("dashboard._load_news_history", return_value=[]), patch("dashboard.init_db"):
            app = AppTest.from_string(self.workspace).run()
            self.assertEqual(app.text_input(key="news_query_km_border_tension").value, "ថៃ កម្ពុជា")
            app.text_input(key="news_query_km_oyoshi").input("អូយ៉ូស៊ី").run()
            app.button(key="collect_news_articles").click().run()
            self.assertEqual(collect.call_args.kwargs["khmer_searches"]["oyoshi"], "អូយ៉ូស៊ី")
            app.multiselect(key="news_editions").set_value(["en"]).run()
            self.assertFalse(any(w.key and w.key.startswith("news_query_km_") for w in app.text_input))
            app.button(key="collect_news_articles").click().run()
        self.assertEqual(collect.call_args.kwargs["editions"], ["en"])

    def test_disabled_categories_are_not_searched(self):
        with patch("dashboard.collect_news", return_value=[]) as collect, \
                patch("dashboard._load_news_history", return_value=[]), patch("dashboard.init_db"):
            app = AppTest.from_string(self.workspace).run()
            app.checkbox(key="news_enabled_oyoshi").uncheck().run()
            app.button(key="collect_news_articles").click().run()
        self.assertNotIn("oyoshi", collect.call_args.args[0])
        self.assertIn("no articles", app.info[0].value)

    def test_trends_risk_history_states(self):
        with patch("dashboard._load_news_history", return_value=[]):
            app = AppTest.from_string(self.history).run()
            self.assertIn("start tracking", app.info[0].value)
        with patch("dashboard._load_news_history", side_effect=RuntimeError("offline")):
            app = AppTest.from_string(self.history).run()
            self.assertIn("could not be loaded", app.warning[0].value)
        rows = [build_scan_snapshot([article("Thai Cambodia border dispute")], searches=SEARCHES, window="7d",
                                    editions=["en"], scanned_at="2026-10-01T00:00:00+00:00"),
                build_scan_snapshot([article("Thai and Cambodian troops clash")] * 10, searches=SEARCHES,
                                    window="7d", editions=["en"], previous={"risk_score": 5.0},
                                    scanned_at="2026-10-02T00:00:00+00:00")]
        with patch("dashboard._load_news_history", return_value=rows):
            app = AppTest.from_string(self.history).run()
        self.assertFalse(list(app.exception))
        metrics = {m.label: m for m in app.metric}
        self.assertEqual(metrics["Latest border tension risk"].value, "100 · High")
        self.assertEqual(metrics["News scans"].value, "2")
        self.assertIn("rising", app.error[0].value)
        self.assertEqual(len(app.get("arrow_vega_lite_chart")), 1)
        self.assertEqual(len(app.dataframe[0].value), 2)


if __name__ == "__main__":
    unittest.main()
