"""Velocity presets, existing-check reuse, and the dedicated card workspace."""

from copy import deepcopy
from types import SimpleNamespace
import re
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from modules import velocity_tracking as store
from modules.client_report import report_document, report_record, velocity_sheet
from modules.database import _get_collection_name
from modules.saved_links import library_owner_id


class MemoryDB:
    def __init__(self):
        self.rows = {}
        self.fail_reads = False
        self.fail_writes = False

    def collection(self, name):
        return MemoryRef(self, name)

    def transaction(self, **kwargs):
        return SimpleNamespace(set=lambda ref, row: ref.set(row))


class MemoryRef:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def document(self, name):
        return MemoryRef(self.db, self.path + "/" + name)

    def collection(self, name):
        return MemoryRef(self.db, self.path + "/" + name)

    def get(self, **kwargs):
        if self.db.fail_reads:
            raise RuntimeError("offline")
        return SimpleNamespace(to_dict=lambda: deepcopy(self.db.rows.get(self.path)))

    def set(self, row):
        if self.db.fail_writes:
            raise RuntimeError("offline")
        self.db.rows[self.path] = deepcopy(row)

    def where(self, *, filter):
        def stream(**kwargs):
            if self.db.fail_reads:
                raise RuntimeError("offline")
            return [SimpleNamespace(id=path.rsplit("/", 1)[1], to_dict=lambda row=row: deepcopy(row))
                    for path, row in self.db.rows.items()
                    if path.rsplit("/", 1)[0] == self.path and row.get(filter.field_path) in filter.value]
        return SimpleNamespace(stream=stream)


def post_url(index=1):
    return f"https://www.facebook.com/example/posts/{index}"


class VelocityTestCase(unittest.TestCase):
    def setUp(self):
        self.db = MemoryDB()
        transaction = patch("modules.velocity_tracking.firestore.transactional", lambda function: function)
        transaction.start()
        self.addCleanup(transaction.stop)


class VelocityStoreTests(VelocityTestCase):
    def test_cards_survive_reload_are_private_and_enforce_limit_against_saved_state(self):
        for index in range(10):
            store.add_card(self.db, " OWNER@example.com ", f"Post {index}", post_url(index))
        self.assertEqual(len(store.load_cards(self.db, "owner@example.com")), 10)
        with self.assertRaisesRegex(ValueError, "up to 10"):
            store.add_card(self.db, "owner@example.com", "Eleventh", post_url(10))
        self.assertEqual(store.load_cards(self.db, "other@example.com"), [])
        store.add_card(self.db, "other@example.com", "Private", post_url(10))
        first = store.load_cards(self.db, "owner@example.com")[0]
        self.db.rows["raw_posts/saved"] = {"url": store.card_posts(first)[0]["url"], "checks": [{"at": "2026-11-01", "comments": 2}]}
        store.remove_card(self.db, "owner@example.com", first["id"])
        store.add_card(self.db, "owner@example.com", "Replacement", post_url(10))
        self.assertEqual(len(store.load_cards(self.db, "owner@example.com")), 10)
        self.assertEqual(store.load_cards(self.db, "other@example.com")[0]["name"], "Private")
        self.assertIn("raw_posts/saved", self.db.rows)

    def test_invalid_and_duplicate_links_do_not_create_cards(self):
        for url in ("https://example.com/posts/1", "https://facebook.com.evil.test/a/posts/1",
                    "https://www.facebook.com/example", "https://www.facebook.com/groups/123/",
                    "javascript:alert(1)", "https://www.facebook.com:81/example/posts/1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                store.add_card(self.db, "owner@example.com", "Name", url)
        self.assertEqual(self.db.rows, {})
        card = store.add_card(self.db, "owner@example.com", "Original", post_url())[0]
        with self.assertRaisesRegex(ValueError, "already in"):
            store.add_post(self.db, "owner@example.com", card["id"], "m.facebook.com/example/posts/1/?fbclid=abc#top")
        self.assertEqual(len(store.load_cards(self.db, "owner@example.com")), 1)

    def test_permalink_video_photo_and_share_identifiers_are_preserved(self):
        for suffix in ("/groups/10/posts/20/", "/groups/10/permalink/20/", "/watch/?v=55",
                       "/reel/44", "/example/videos/55/", "/photo.php?fbid=22",
                       "/story.php?story_fbid=12&id=34", "/share/p/abc/"):
            self.assertTrue(store.normalize_post_url("https://m.facebook.com" + suffix)
                            .startswith("https://www.facebook.com/"))
        self.assertTrue(store.normalize_post_url("facebook.com/watch/?v=55&ref=feed").endswith("?v=55"))

    def test_existing_checks_load_without_analysis_and_match_mobile_permalink_variants(self):
        cards = store.add_card(self.db, "owner@example.com", "Launch", post_url())
        cards = store.add_card(self.db, "owner@example.com", "Uncollected", post_url(2))
        self.db.rows[f"{_get_collection_name()}/mobile"] = {
            "url": "https://m.facebook.com/example/posts/1/", "raw_text": "First capture",
            "checks": [{"at": "2026-11-01", "comments": 10}],
        }
        self.db.rows[f"{_get_collection_name()}/desktop"] = {
            "url": post_url(), "raw_text": "Later capture", "analysis": {"sentiment": "Joy"},
            "checks": [{"at": "2026-11-02", "comments": 20}],
        }
        rows = store.load_card_records(self.db, cards)
        post_id = store.card_posts(cards[0])[0]["id"]
        self.assertEqual(list(rows), [post_id])
        self.assertEqual([check["comments"] for check in rows[post_id]["checks"]], [10, 20])
        sheet = velocity_sheet([report_record(rows[post_id])], "", negative_only=False)
        self.assertIn("alive</span>", sheet)

    def test_empty_table_has_no_invented_checks_and_sentiment_export_has_no_velocity(self):
        record = report_record({"url": post_url(), "raw_text": "<script>alert(1)</script>"})
        sheet = velocity_sheet([record], "", negative_only=False)
        self.assertEqual(re.findall(r"<th scope=col>(.*?)</th>", sheet),
                         ["Provoker's post", *[f"Check {i}" for i in range(1, 9)], "Status"])
        self.assertNotIn('class="crt-status ', sheet)
        self.assertNotIn("<script>", sheet)
        self.assertEqual(record["checks"], [])
        record["sentiment"] = "Anger"
        document = report_document([record], "2026-11-07")
        self.assertNotIn('aria-label="Velocity Tracker"', document)
        self.assertNotIn("Velocity vs last 2", document)

    def test_blank_cards_have_unique_stable_ids_and_do_not_query_posts(self):
        for _ in range(2):
            store.add_card(self.db, "owner@example.com", url="  ")
        cards = store.load_cards(self.db, "owner@example.com")
        self.assertNotEqual(cards[0]["id"], cards[1]["id"])
        db = Mock()
        self.assertEqual(store.load_card_records(db, cards), {})
        db.collection.assert_not_called()
        self.assertEqual(store.load_cards(self.db, "owner@example.com"), cards)

    def test_each_card_holds_ten_posts_and_removing_a_row_keeps_collected_data(self):
        card = store.add_card(self.db, "owner@example.com")[0]
        other = store.add_card(self.db, "other@example.com")[0]
        for index in range(10):
            store.add_post(self.db, "owner@example.com", card["id"], post_url(index))
        with self.assertRaisesRegex(ValueError, "up to 10 posts"):
            store.add_post(self.db, "owner@example.com", card["id"], post_url(10))
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            store.add_post(self.db, "other@example.com", card["id"], post_url(10))
        first = store.card_posts(store.load_cards(self.db, "owner@example.com")[0])[0]
        self.db.rows["raw_posts/saved"] = {"url": first["url"], "checks": [{"at": "2026-11-01", "comments": 3}]}
        store.remove_post(self.db, "owner@example.com", card["id"], first["id"])
        store.add_post(self.db, "owner@example.com", card["id"], post_url(10))
        self.assertEqual(len(store.card_posts(store.load_cards(self.db, "owner@example.com")[0])), 10)
        self.assertEqual(store.load_cards(self.db, "other@example.com"), [other])
        self.assertIn("raw_posts/saved", self.db.rows)

    def test_legacy_single_post_card_can_gain_rows_without_losing_its_id_or_post(self):
        legacy = {"id": "a" * 64, "name": "Existing card", "url": post_url(), "source_url": post_url()}
        key = "sensemaker_velocity_tracking/" + library_owner_id("owner@example.com")
        self.db.rows[key] = {"cards": [legacy]}
        saved = store.add_post(self.db, "owner@example.com", legacy["id"], post_url(2))[0]
        self.assertEqual((saved["id"], saved["name"]), (legacy["id"], legacy["name"]))
        self.assertEqual([post["url"] for post in store.card_posts(saved)], [post_url(), post_url(2)])

    def test_rename_keeps_posts_and_runs_validates_and_stays_private(self):
        card = store.add_card(self.db, "owner@example.com", url=post_url())[0]
        other = store.add_card(self.db, "other@example.com")[0]
        post = store.card_posts(card)[0]
        store.save_run(self.db, "owner@example.com", card["id"], {
            "id": "b" * 64, "at": "2026-11-07T01:00:00+00:00",
            "results": [{"post_id": post["id"], "views": 100, "outcome": "ok"}]})
        before = store.load_cards(self.db, "owner@example.com")[0]
        saved = store.rename_card(self.db, "owner@example.com", card["id"], "  Border   clash  posts ")[0]
        self.assertEqual(saved["name"], "Border clash posts")
        self.assertEqual({k: v for k, v in saved.items() if k != "name"},
                         {k: v for k, v in before.items() if k != "name"})
        for name, message in (("   ", "Enter a card name"), ("x" * 121, "at most 120")):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                store.rename_card(self.db, "owner@example.com", card["id"], name)
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            store.rename_card(self.db, "other@example.com", card["id"], "Someone else's")
        with self.assertRaisesRegex(ValueError, "Choose a saved"):
            store.rename_card(self.db, "owner@example.com", "not-an-id", "Name")
        self.assertEqual(store.load_cards(self.db, "owner@example.com")[0]["name"], "Border clash posts")
        self.assertEqual(store.load_cards(self.db, "other@example.com"), [other])

    def test_table_status_labels_are_alive_dying_or_dead(self):
        for values, expected in (((10, 20), "alive"), ((10, 30, 35), "dying"), ((10, 10, 10), "dead")):
            row = report_record({"url": post_url(), "checks": [
                {"at": f"2026-11-{index + 1:02}", "comments": value} for index, value in enumerate(values)]})
            sheet = velocity_sheet([row], "", negative_only=False)
            self.assertIn(f">{expected}</span>", sheet)


class VelocityUITests(VelocityTestCase):
    script = "from modules.velocity_tracking_ui import render_velocity_workspace\nrender_velocity_workspace('owner@example.com')"
    key = f"velocity_tracking_{library_owner_id('owner@example.com')}"

    def setUp(self):
        super().setUp()
        database = patch("modules.velocity_tracking_ui.database.init_db", return_value=self.db)
        database.start()
        self.addCleanup(database.stop)
        for target, value in (("modules.auth.verify_access", "owner@example.com"),
                              ("modules.auth.allowed_emails", {"owner@example.com"})):
            auth = patch(target, return_value=value)
            auth.start()
            self.addCleanup(auth.stop)

    def app(self):
        app = AppTest.from_string(self.script).run()
        self.assertFalse(list(app.exception))
        return app

    def test_create_blank_cards_reload_and_add_posts_later(self):
        app = self.app()
        self.assertTrue(any("No velocity cards yet" in message.value for message in app.info))
        self.assertEqual(len(app.text_input), 0)
        for _ in range(2):
            app.button(key=f"{self.key}_create").click().run()
            self.assertFalse(list(app.exception))
        cards = store.load_cards(self.db, "owner@example.com")
        self.assertEqual([store.card_posts(card) for card in cards], [[], []])
        fresh = self.app()
        self.assertEqual(len([button for button in fresh.button if button.key and "_open_" in button.key]), 2)
        self.assertFalse(any('aria-label="Velocity Tracker"' in element.proto.body for element in fresh.get("html")))
        card = cards[0]
        fresh.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertFalse(list(fresh.exception))
        for index in range(2):
            fresh.text_input(key=f"{self.key}_{card['id']}_url_{index}").set_value(post_url(index))
            next(button for button in fresh.button
                 if button.key and f"{card['id']}_add_post_{index}" in button.key).click().run()
            self.assertFalse(list(fresh.exception))
        saved = store.load_cards(self.db, "owner@example.com")
        self.assertEqual([len(store.card_posts(row)) for row in saved], [2, 0])
        self.db.rows[f"{_get_collection_name()}/post"] = {
            "url": post_url(0), "checks": [{"at": "2026-11-01", "comments": 10},
                                          {"at": "2026-11-02", "comments": 30}],
        }
        first_post = store.card_posts(saved[0])[0]
        for index, views in enumerate((100, 200)):
            store.save_run(self.db, "owner@example.com", card["id"], {
                "id": str(index) * 64, "at": f"2026-11-0{index + 1}",
                "results": [{"post_id": first_post["id"], "views": views, "outcome": "ok"}],
            })
        fresh.button(key=f"{self.key}_refresh").click().run()
        self.assertTrue(any("alive</span>" in element.proto.body for element in fresh.get("html")))
        fresh.button(key=f"{self.key}_{card['id']}_remove_post").click().run()
        self.assertEqual(len(store.card_posts(store.load_cards(self.db, "owner@example.com")[0])), 1)
        self.assertIn(f"{_get_collection_name()}/post", self.db.rows)
        fresh.button(key=f"{self.key}_remove_{card['id']}").click().run()
        self.assertEqual(len(store.load_cards(self.db, "owner@example.com")), 1)

    def test_rename_card_from_notepad_updates_its_cover(self):
        card = store.add_card(self.db, "owner@example.com")[0]
        app = self.app()
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        name = app.text_input(key=f"{self.key}_{card['id']}_name_0")
        self.assertEqual(name.value, "Velocity tracking card 1")
        name.set_value("Border clash posts")
        next(button for button in app.button
             if button.key and f"{card['id']}_rename_0" in button.key).click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(store.load_cards(self.db, "owner@example.com")[0]["name"], "Border clash posts")
        self.assertTrue(any("Card renamed" in message.value for message in app.success))
        self.assertEqual(app.text_input(key=f"{self.key}_{card['id']}_name_1").value, "Border clash posts")
        self.assertTrue(any("<h3>Border clash posts</h3>" in element.proto.body for element in app.get("html")))
        app.text_input(key=f"{self.key}_{card['id']}_name_1").set_value("   ")
        next(button for button in app.button
             if button.key and f"{card['id']}_rename_1" in button.key).click().run()
        self.assertTrue(any("Enter a card name" in error.value for error in app.error))
        self.assertEqual(store.load_cards(self.db, "owner@example.com")[0]["name"], "Border clash posts")

    def test_failed_create_and_load_do_not_falsely_add_a_card(self):
        app = self.app()
        self.db.fail_writes = True
        app.button(key=f"{self.key}_create").click().run()
        self.assertTrue(any("could not be saved" in error.value for error in app.error))
        self.assertEqual(store.load_cards(self.db, "owner@example.com"), [])
        self.db.fail_reads = True
        app.button(key=f"{self.key}_refresh").click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(app.button(key=f"{self.key}_create").disabled)

    def test_dialog_checks_access_before_showing_post_controls(self):
        card = store.add_card(self.db, "owner@example.com", url=post_url())[0]
        app = self.app()
        with patch("modules.auth.verify_access", return_value=None):
            app.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertFalse(list(app.exception))
        self.assertFalse(any(button.label in {"Run", "Remove card"} for button in app.button))
        self.assertFalse(any('aria-label="Velocity Tracker"' in element.proto.body for element in app.get("html")))

    def test_ten_blank_cards_disable_create_and_show_covers_until_opened(self):
        for _ in range(10):
            store.add_card(self.db, "owner@example.com")
        app = self.app()
        self.assertEqual(len([button for button in app.button if button.key and "_open_" in button.key]), 10)
        self.assertEqual(len([button for button in app.button if button.label == "Remove card"]), 0)
        self.assertTrue(app.button(key=f"{self.key}_create").disabled)
        self.assertEqual(len([element for element in app.get("html")
                              if 'aria-label="Velocity Tracker"' in element.proto.body]), 0)


if __name__ == "__main__":
    unittest.main()
