"""Tests for global high-velocity (>0.5M views) and negative sentiment (>50%) alerts across all UI pages."""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules.client_report import report_record, velocity_sheet
from modules.engagement import velocity
from modules.facebook_session import FacebookSession
from modules.global_alerts import (
    NEGATIVE_SENTIMENT_ALERT_THRESHOLD,
    VELOCITY_VIEW_ALERT_THRESHOLD,
    find_high_velocity_posts,
    find_negative_sentiment_flags,
    format_negative_sentiment_alert,
    format_velocity_alert,
)
from modules.velocity_table import velocity_card_sheet, view_status
from modules.velocity_tracking import card_posts


class GlobalAlertsUnitTests(unittest.TestCase):
    def test_velocity_threshold_stays_silent_at_or_below_half_million_and_alerts_above(self):
        self.assertEqual(VELOCITY_VIEW_ALERT_THRESHOLD, 500_000)
        card = {
            "id": "a" * 64,
            "name": "Launch monitoring",
            "url": "https://www.facebook.com/example/posts/1",
        }
        post_id = card_posts(card)[0]["id"]

        card["runs"] = [{"at": "2026-11-07T10:00:00Z", "results": [{"post_id": post_id, "views": 500_000}]}]
        self.assertEqual(find_high_velocity_posts([card]), [])

        card["runs"].append({"at": "2026-11-07T11:00:00Z", "results": [{"post_id": post_id, "views": 500_001}]})
        flagged = find_high_velocity_posts([card])
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["views"], 500_001)
        self.assertEqual(flagged[0]["card_name"], "Launch monitoring")
        msg = format_velocity_alert(flagged)
        self.assertIn("exceeded 0.5M (500,000) views", msg)
        self.assertIn("500,001 views in Launch monitoring", msg)

    def test_velocity_checks_collected_posts_when_not_in_cards(self):
        posts = [
            {
                "url": "https://www.facebook.com/example/posts/viral",
                "source_type": "container_post",
                "checks": [{"at": "2026-11-07T12:00:00Z", "views": 750_000, "comments": 120}],
            }
        ]
        flagged = find_high_velocity_posts([], posts)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["views"], 750_000)
        self.assertIn("750,000 views", format_velocity_alert(flagged))

    def test_negative_sentiment_threshold_flags_strictly_above_fifty_percent(self):
        self.assertEqual(NEGATIVE_SENTIMENT_ALERT_THRESHOLD, 0.5)
        # Exactly 50% (2 of 4 negative) should not trigger
        balanced = {
            "analyses": [
                {"sentiment": "Anger"},
                {"sentiment": "Anxiety"},
                {"sentiment": "Joy"},
                {"sentiment": "Neutral"},
            ]
        }
        self.assertEqual(find_negative_sentiment_flags(analysis_results=balanced), [])

        # 60% (3 of 5 negative) should trigger
        majority_negative = {
            "analyses": [
                {"sentiment": "Anger"},
                {"sentiment": "Anxiety"},
                {"sentiment": "Anger"},
                {"sentiment": "Joy"},
                {"sentiment": "Neutral"},
            ]
        }
        flags = find_negative_sentiment_flags(analysis_results=majority_negative)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["source"], "AI Sentiment Analysis")
        self.assertEqual(flags[0]["negative_pct"], 60.0)
        msg = format_negative_sentiment_alert(flags)
        self.assertIn("Negative sentiment exceeded 50%", msg)
        self.assertIn("AI Sentiment Analysis at 60.0% negative (3 of 5 records)", msg)

    def test_online_news_negative_sentiment_flag(self):
        news_scan = {"tone_counts": {"Positive": 1, "Neutral": 2, "Negative": 5}}
        flags = find_negative_sentiment_flags(news_scan=news_scan)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["source"], "Online News")
        self.assertEqual(flags[0]["negative_pct"], 62.5)
        self.assertIn("Online News at 62.5% negative (5 of 8 records)", format_negative_sentiment_alert(flags))

    def test_significant_velocity_increase_over_time_has_status_viral(self):
        # Moderate growth remains "alive"
        self.assertEqual(view_status([100, 200]), "alive")
        self.assertEqual(view_status([100, 150, 200]), "alive")

        # Significant surge (>= 200% jump, >= 2x velocity acceleration, >= 10k delta, or > 500k views) becomes "Viral"
        self.assertEqual(view_status([100, 400]), "Viral")
        self.assertEqual(view_status([100, 200, 500]), "Viral")
        self.assertEqual(view_status([5_000, 20_000]), "Viral")
        self.assertEqual(view_status([450_000, 550_000]), "Viral")

        card = {
            "id": "b" * 64,
            "name": "Viral card",
            "url": "https://www.facebook.com/example/posts/surge",
        }
        post_id = card_posts(card)[0]["id"]
        card["runs"] = [
            {"at": "2026-11-07T10:00:00Z", "results": [{"post_id": post_id, "views": 100, "outcome": "ok"}]},
            {"at": "2026-11-07T11:00:00Z", "results": [{"post_id": post_id, "views": 200, "outcome": "ok"}]},
            {"at": "2026-11-07T12:00:00Z", "results": [{"post_id": post_id, "views": 500, "outcome": "ok"}]},
        ]
        sheet = velocity_card_sheet(card)
        self.assertIn('<span class="crt-status crt-status--viral">Viral</span>', sheet)

        # Engagement checks velocity also marks significant increases as "Viral"
        checks = [
            {"at": "2026-11-07T10:00:00Z", "comments": 20},
            {"at": "2026-11-07T11:00:00Z", "comments": 40},
            {"at": "2026-11-07T12:00:00Z", "comments": 120},
        ]
        self.assertEqual(velocity(checks)["status"], "Viral")
        report = velocity_sheet([report_record({"url": "https://www.facebook.com/example/posts/surge", "checks": checks})], "", negative_only=False)
        self.assertIn('<span class="crt-status crt-status--viral">Viral</span>', report)


class GlobalAlertsDashboardUITests(unittest.TestCase):
    def test_main_renders_velocity_and_negative_sentiment_alerts_above_all_ui_pages(self):
        card = {
            "id": "a" * 64,
            "name": "Viral watch",
            "url": "https://www.facebook.com/example/posts/viral",
        }
        post_id = card_posts(card)[0]["id"]
        card["runs"] = [{"at": "2026-11-07T12:00:00Z", "results": [{"post_id": post_id, "views": 620_000, "outcome": "ok"}]}]
        sentiment_rows = [{
            "run_id": "run-1",
            "completed_at": "2026-11-07T12:00:00Z",
            "counts": {"Joy": 1, "Neutral": 1, "Anger": 4, "Anxiety": 2},
            "scored_count": 8,
            "processed_count": 8,
            "error_count": 0,
            "score": -62.5,
            "provider": "gemini",
            "source": "AI analysis",
        }]

        with ExitStack() as stack:
            stack.enter_context(patch("dashboard.require_access", return_value="editor@example.com"))
            stack.enter_context(patch("dashboard.IS_CLOUD", False))
            stack.enter_context(patch("dashboard._get_facebook_session", return_value=FacebookSession()))
            stack.enter_context(patch("dashboard.render_browserbase_usage_bar"))
            stack.enter_context(patch("dashboard._load_sentiment_history", return_value=sentiment_rows))
            stack.enter_context(patch("dashboard._load_news_history", return_value=[]))
            stack.enter_context(patch("modules.velocity_tracking.load_cards", return_value=[card]))
            stack.enter_context(patch("modules.velocity_tracking.load_workspace_cards", return_value=[card]))
            stack.enter_context(patch("modules.velocity_tracking_ui.database.init_db"))
            for name in ("_render_scrape_workspace", "render_record_management", "_render_analysis_tab",
                         "_render_sentiment_trends", "render_content_workspace",
                         "render_gencite_workspace", "render_tutorial_workspace"):
                stack.enter_context(patch("dashboard." + name))

            app = AppTest.from_string("from dashboard import main\nmain()", default_timeout=30).run()
            self.assertFalse(list(app.exception))
            warnings = [w.value for w in app.warning]
            self.assertTrue(any("exceeded 0.5M (500,000) views" in w and "620,000 views in Viral watch" in w for w in warnings))
            self.assertTrue(any("Negative sentiment exceeded 50%" in w and "75.0% negative (6 of 8 records)" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
