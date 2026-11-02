"""Saving a collection records a velocity check without changing saved posts or analyses."""

import unittest
from unittest.mock import MagicMock

from modules.database import upsert_post
from modules.reddit import _record

FOOTER = "Stop buying Thai goods\n{comments} comments\nLike"


def database(previous=None):
    db = MagicMock()
    ref = db.collection.return_value.document.return_value
    ref.get.return_value.exists = previous is not None
    ref.get.return_value.to_dict.return_value = previous or {}
    return db, ref


class EngagementPersistenceTests(unittest.TestCase):
    def test_new_post_starts_with_its_collection_as_check_one(self):
        db, ref = database()
        upsert_post(db, {"url": "https://www.facebook.com/groups/1/posts/2/", "raw_text": FOOTER.format(comments=8),
                         "comment_count": 3, "source_type": "container_post", "scraped_at": "2026-11-01T00:00:00"})
        self.assertEqual(ref.set.call_args.args[0]["checks"],
                         [{"at": "2026-11-01T00:00:00", "collected_comments": 3, "comments": 8}])

    def test_recollected_post_keeps_text_and_analysis_and_adds_a_check(self):
        previous = {"raw_text": FOOTER.format(comments=8), "comment_count": 3, "status": "processed",
                    "analysis": {"sentiment": "Anger"}, "scraped_at": "2026-11-01T00:00:00"}
        db, ref = database(previous)
        upsert_post(db, {"url": "https://www.facebook.com/groups/1/posts/2/", "raw_text": FOOTER.format(comments=20),
                         "comment_count": 5, "source_type": "container_post", "scraped_at": "2026-11-01T06:00:00"})
        ref.set.assert_called_once()
        self.assertEqual(ref.set.call_args.kwargs, {"merge": True})
        self.assertEqual(set(ref.set.call_args.args[0]), {"checks"})
        self.assertEqual([c["comments"] for c in ref.set.call_args.args[0]["checks"]], [8, 20])

    def test_news_articles_and_reddit_comments_have_no_checks(self):
        for source_type in ("news_article", "reddit_comment"):
            db, ref = database()
            upsert_post(db, {"url": "https://example.com/a", "raw_text": "Text", "source_type": source_type})
            self.assertNotIn("checks", ref.set.call_args.args[0])

    def test_reddit_posts_store_the_thread_total(self):
        data = {"id": "abc", "subreddit": "cambodia", "title": "Thai shops", "selftext": "", "num_comments": 57,
                "created_utc": 1700000000}
        post = _record(data, "t3")
        self.assertEqual(post["reported_comment_count"], 57)
        db, ref = database()
        upsert_post(db, post)
        payload = ref.set.call_args.args[0]
        self.assertEqual(payload["reported_comment_count"], 57)
        self.assertEqual(payload["checks"][0]["comments"], 57)


if __name__ == "__main__":
    unittest.main()
