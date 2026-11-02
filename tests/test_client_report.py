"""Engagement checks, velocity and the client's report templates."""

import unittest

from modules.client_report import (
    capture_text, content_type, negative_records, npr_sheet, npr_view, platform_label, report_document,
    report_record, sentiment_sheet, sentiment_view, threat_level, velocity_sheet, velocity_view, word_cloud,
)
from modules.engagement import add_check, checks_for, engagement_from_text, parse_count, snapshot, velocity

FOOTER = "Page name\nStop buying Thai goods\nAll reactions:\n1.2K\n{comments} comments\n56 shares\nLike\nComment\nShare"


def monitor(signal="none", relevance="explicit", **kwargs):
    return {"thailand_relevance": relevance, "signal_type": signal, "target_names": [], "confidence": "medium",
            "summary": "", "evidence": [], **kwargs}


def record(ident, sentiment="Neutral", risk=1, signal="none", relevance="explicit", **fields):
    return report_record({
        "_id": ident, "url": f"https://www.facebook.com/groups/1/posts/{ident}/", "source_type": "container_post",
        "analysis": {"sentiment": sentiment, "risk_score": risk, "topics": ["border"], "translation": "Border news",
                     "monitor": monitor(signal, relevance)},
        **fields,
    })


def checks(*values, key="comments"):
    return [{"at": f"2026-11-0{i + 1}T00:00:00+00:00", key: value, "collected_comments": 3}
            for i, value in enumerate(values)]


class EngagementTests(unittest.TestCase):
    def test_parses_displayed_counts(self):
        self.assertEqual(parse_count("1.2K"), 1200)
        self.assertEqual(parse_count("1,234"), 1234)
        self.assertEqual(parse_count("3M"), 3_000_000)
        self.assertEqual(parse_count("1,5K"), 1500)
        self.assertIsNone(parse_count("many"))

    def test_reads_footer_counts_but_not_sentences(self):
        found = engagement_from_text(FOOTER.format(comments=345) + "\n12K views")
        self.assertEqual(found, {"comments": 345, "shares": 56, "views": 12000, "reactions": 1200})
        self.assertEqual(engagement_from_text("I wrote 3 comments about this boycott"), {})
        self.assertEqual(engagement_from_text("1.2K comments · 56 shares"), {"comments": 1200, "shares": 56})

    def test_reddit_total_beats_collected_sample(self):
        check = snapshot({"comment_count": 20, "reported_comment_count": 57, "raw_text": "Title"}, "t")
        self.assertEqual(check, {"at": "t", "collected_comments": 20, "comments": 57})

    def test_checks_seed_from_first_collection_and_replace_resaves(self):
        previous = {"raw_text": FOOTER.format(comments=10), "comment_count": 4, "scraped_at": "2026-11-01T00:00:00"}
        self.assertEqual([c["comments"] for c in checks_for(previous)], [10])
        later = add_check(previous, {"raw_text": FOOTER.format(comments=25), "comment_count": 6}, "2026-11-02T00:00:00")
        self.assertEqual([c["comments"] for c in later], [10, 25])
        resaved = add_check({"checks": later}, {"raw_text": FOOTER.format(comments=30)}, "2026-11-02T00:00:00")
        self.assertEqual([c["comments"] for c in resaved], [10, 30])
        self.assertEqual(checks_for({"source_type": "news_article", "scraped_at": "2026-11-01"}), [])

    def test_velocity_status_follows_the_template_key(self):
        self.assertIsNone(velocity(checks(5))["status"])
        self.assertEqual(velocity(checks(5, 9, 20))["status"], "Live")
        self.assertEqual(velocity(checks(5, 20, 25))["status"], "Slowing")
        self.assertEqual(velocity(checks(5, 20, 20))["status"], "Slowing")
        self.assertEqual(velocity(checks(20, 20, 20))["status"], "Dead")
        result = velocity(checks(40, 50))
        self.assertEqual((result["change"], result["pct"], result["direction"]), (10, 25.0, "up"))

    def test_velocity_uses_collected_comments_only_without_platform_totals(self):
        result = velocity([{"at": "a", "collected_comments": 2}, {"at": "b", "collected_comments": 5}])
        self.assertEqual((result["metric"], result["change"]), ("collected_comments", 3))


class ClientReportTests(unittest.TestCase):
    def test_sentiment_shares_add_to_100_and_threat_follows_signals(self):
        records = [record("a", "Anger", 9, "boycott_call"), record("b", "Joy"), record("c"), record("d", "Anxiety", 4)]
        view = sentiment_view(records)
        self.assertEqual(view["shares"], {"Negative": 50, "Neutral": 25, "Positive": 25})
        self.assertEqual(view["threat"], "High")
        self.assertIn("1 explicit boycott call", view["insight"][0])
        self.assertEqual(threat_level([record("x", "Anger", 6)]), "Medium")
        self.assertEqual(threat_level([record("x", "Joy", 2)]), "Low")
        thirds = sentiment_view([record("a", "Anger"), record("b", "Joy"), record("c")])["shares"]
        self.assertEqual(sum(thirds.values()), 100)

    def test_velocity_tracker_keeps_negative_posts_and_compares_matching_checks(self):
        records = [
            record("a", "Anger", 7, checks=checks(10, 20, 25)),
            record("b", "Anxiety", 5, checks=checks(4, 8)),
            record("c", "Joy", 1, checks=checks(1, 100)),
            report_record({"_id": "d", "source_type": "reddit_comment", "url": "https://www.reddit.com/r/x/c",
                           "analysis": {"sentiment": "Anger", "monitor": monitor()}}),
        ]
        view = velocity_view(records)
        self.assertEqual([row["record"]["_id"] for row in view["rows"]], ["a", "b"])
        self.assertEqual(view["totals"], [14, 28, 25])
        self.assertEqual(view["change_abs"], [None, 14, 5])
        self.assertEqual(view["change_pct"][2], 25.0)
        self.assertIn("2 negative posts tracked: 1 live, 1 slowing.", view["assessment"])

    def test_negative_post_report_uses_thread_and_leaves_uncollected_fields_blank(self):
        post = report_record({
            "_id": "abcdef123", "url": "https://www.reddit.com/r/cambodia/comments/abc/", "platform": "reddit",
            "source_type": "reddit_post", "created_at": "2026-10-30T11:20:00+00:00",
            "checks": checks(31, 57),
            "analysis": {"sentiment": "Anger", "risk_score": 8, "topics": ["boycott"], "translation": "Boycott Thai shops",
                         "monitor": monitor("boycott_call", summary="Calls for a boycott")},
        })
        reply = report_record({"_id": "r1", "url": "https://www.reddit.com/r/cambodia/comments/abc/_/r1/",
                               "parent_url": post["url"], "source_type": "reddit_comment",
                               "analysis": {"sentiment": "Joy", "monitor": monitor(), "translation": "Shops are fine"}})
        view = npr_view(post, [post, reply])
        self.assertEqual(view["number"], "NPR-ABCDEF")
        self.assertEqual(view["shares"], {"Negative": 50, "Neutral": 0, "Positive": 50})
        self.assertEqual((view["impact"], view["cadence"], view["direction"]), ("High", "6h", "up"))
        self.assertEqual(view["comments"], (57, 31))
        html = npr_sheet(post, [post, reply], "2026-11-02")
        self.assertIn("Not collected", html)
        self.assertIn("Calls for a boycott", html)
        self.assertEqual(negative_records([reply, post]), [post])

    def test_untrusted_text_is_escaped_and_links_are_http_only(self):
        bad = record("x", "Anger", 9, "boycott_call", raw_text="<script>alert(1)</script>",
                     url="javascript:alert(1)")
        document = report_document([bad], "2026-11-02T00:00:00+00:00")
        self.assertNotIn("<script>", document)
        self.assertNotIn("javascript:", document)
        self.assertIn("&lt;script&gt;", document)
        for sheet in (sentiment_sheet([bad], ""), velocity_sheet([bad], "")):
            self.assertNotIn("<script>", sheet)

    def test_labels_capture_and_word_cloud(self):
        video = report_record({"url": "https://www.facebook.com/watch/?v=1", "analysis": {}})
        self.assertEqual((platform_label(video), content_type(video)), ("Facebook", "Video"))
        news = report_record({"source_type": "news_article", "publisher": "Khmer Times", "url": "https://k.com/a"})
        self.assertEqual((platform_label(news), content_type(news)), ("Khmer Times", "News article"))
        self.assertEqual(capture_text({"raw_text": FOOTER.format(comments=3)}), "Page name Stop buying Thai goods")
        cloud = dict(word_cloud(["Boycott Thai brands. Thai brands must leave; boycott Thai petrol."]))
        self.assertEqual(cloud["thai"], 5)
        self.assertNotIn("must", cloud)


if __name__ == "__main__":
    unittest.main()
