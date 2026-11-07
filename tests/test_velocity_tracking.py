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
        def stream(fields=None, **kwargs):
            if self.db.fail_reads:
                raise RuntimeError("offline")
            return [SimpleNamespace(id=path.rsplit("/", 1)[1],
                                    to_dict=lambda row=row: deepcopy({key: value for key, value in row.items()
                                                                    if fields is None or key in fields}))
                    for path, row in self.db.rows.items()
                    if path.rsplit("/", 1)[0] == self.path and
                    (row.get(filter.field_path) == filter.value if filter.op_string == "=="
                     else row.get(filter.field_path) in filter.value)]
        return SimpleNamespace(stream=stream, select=lambda fields: SimpleNamespace(stream=lambda **kwargs: stream(fields, **kwargs)))


def post_url(index=1):
    return f"https://www.facebook.com/example/posts/{index}"


class VelocityTestCase(unittest.TestCase):
    def setUp(self):
        self.db = MemoryDB()
        transaction = patch("modules.velocity_tracking.firestore.transactional", lambda function: function)
        transaction.start()
        self.addCleanup(transaction.stop)


class VelocityStoreTests(VelocityTestCase):
    def test_remarks_are_per_card_post_and_survive_new_checks(self):
        email = "owner@example.com"
        first = store.add_card(self.db, email, url=post_url())[0]
        second = store.add_card(self.db, email, url=post_url())[1]
        first = store.add_post(self.db, email, first["id"], post_url(2))[0]
        post = store.card_posts(first)[0]
        note = "Watch replies\nFollow up tomorrow"
        store.save_remark(self.db, email, first["id"], post["id"], note)
        run = {"id": "f" * 64, "at": "2026-11-07", "results": [{"post_id": post["id"], "views": 100}]}
        store.save_run(self.db, email, first["id"], run)
        first, unchanged = store.load_cards(self.db, email)
        self.assertEqual(store.card_posts(first)[0]["remark"], note)
        self.assertNotIn("remark", store.card_posts(first)[1])
        self.assertEqual(unchanged, second)
        store.save_remark(self.db, email, first["id"], post["id"], "Updated")
        updated = store.load_cards(self.db, email)[0]
        self.assertEqual(updated["runs"], first["runs"])
        store.save_remark(self.db, email, first["id"], post["id"], "")
        self.assertEqual(store.card_posts(store.load_cards(self.db, email)[0])[0]["remark"], "")

    def test_remarks_reject_other_owners_missing_posts_and_oversized_text(self):
        card = store.add_card(self.db, "owner@example.com", url=post_url())[0]
        post = store.card_posts(card)[0]
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            store.save_remark(self.db, "other@example.com", card["id"], post["id"], "Not mine")
        with self.assertRaisesRegex(ValueError, "no longer in"):
            store.save_remark(self.db, "owner@example.com", card["id"], "e" * 64, "Unknown post")
        with self.assertRaisesRegex(ValueError, "at most"):
            store.save_remark(self.db, "owner@example.com", card["id"], post["id"], "x" * (store.MAX_REMARK_CHARS + 1))
        self.assertEqual(store.load_cards(self.db, "owner@example.com"), [card])

    def test_approved_users_see_existing_teammate_cards_and_saved_results(self):
        own = store.add_card(self.db, "owner@example.com", "My card")[0]
        team = store.add_card(self.db, "other@example.com", "Team card", post_url())[0]
        store.add_card(self.db, "outside@example.com", "Unapproved account")
        post = store.card_posts(team)[0]
        store.save_run(self.db, "other@example.com", team["id"], {
            "id": "a" * 64, "at": "2026-11-07T01:00:00+00:00",
            "results": [{"post_id": post["id"], "views": 250, "outcome": "ok"}],
        })
        original = deepcopy(self.db.rows)
        with patch("modules.auth.allowed_emails", return_value={"owner@example.com", "other@example.com"}):
            cards = store.load_workspace_cards(self.db, "owner@example.com")
            self.assertEqual([card["name"] for card in cards], ["My card", "Team card"])
            self.assertEqual(cards[0], own)
            self.assertTrue(cards[1]["shared"])
            self.assertEqual(cards[1]["runs"][0]["results"][0]["views"], 250)
            self.assertEqual(len(store.load_workspace_cards(self.db, "other@example.com")), 2)
            with self.assertRaises(PermissionError):
                store.load_workspace_cards(self.db, "outside@example.com")
        self.assertEqual(self.db.rows, original)
        self.assertEqual(store.load_cards(self.db, "owner@example.com"), [own])

    def test_shared_legacy_cards_with_the_same_id_have_distinct_display_ids(self):
        legacy = {"id": "a" * 64, "name": "Velocity tracking card", "url": post_url()}
        for email in ("owner@example.com", "other@example.com"):
            self.db.rows["sensemaker_velocity_tracking/" + library_owner_id(email)] = {"cards": [legacy]}
        with patch("modules.auth.allowed_emails", return_value={"owner@example.com", "other@example.com"}):
            cards = store.load_workspace_cards(self.db, "owner@example.com")
            again = store.load_workspace_cards(self.db, "owner@example.com")
        self.assertEqual(cards, again)
        self.assertEqual(len({card["id"] for card in cards}), 2)
        self.assertEqual(cards[1]["name"], "Velocity tracking card 1")

    def test_shared_deletion_targets_creator_and_validates_access(self):
        legacy = {"id": "a" * 64, "name": "Same ID", "posts": []}
        for email in ("owner@example.com", "other@example.com"):
            self.db.rows["sensemaker_velocity_tracking/" + library_owner_id(email)] = {"cards": [legacy]}
        with patch("modules.auth.allowed_emails", return_value={"owner@example.com", "other@example.com"}):
            shared = store.load_workspace_cards(self.db, "owner@example.com")[1]
            before = deepcopy(self.db.rows)
            with self.assertRaises(PermissionError):
                store.remove_workspace_card(self.db, "outside@example.com", shared)
            with self.assertRaises(PermissionError):
                store.remove_workspace_card(self.db, "owner@example.com", {**shared, "owner_id": "unknown"})
            with self.assertRaises(ValueError):
                store.remove_workspace_card(self.db, "owner@example.com", {**shared, "source_card_id": "b" * 64})
            self.assertEqual(self.db.rows, before)
            store.remove_workspace_card(self.db, "owner@example.com", shared)
            self.assertEqual(store.load_cards(self.db, "other@example.com"), [])
            self.assertEqual(store.load_cards(self.db, "owner@example.com"), [legacy])
            store.remove_workspace_card(self.db, "owner@example.com", legacy)
            self.assertEqual(store.load_cards(self.db, "owner@example.com"), [])

    def test_rename_rejects_invalid_names_other_accounts_and_removed_cards(self):
        card = store.add_card(self.db, "owner@example.com", "Original", post_url())[0]
        original = deepcopy(self.db.rows)
        for name in ("", "   ", "x" * (store.MAX_NAME_CHARS + 1)):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "card name"):
                store.rename_card(self.db, "owner@example.com", card["id"], name)
        with self.assertRaisesRegex(ValueError, "saved velocity card"):
            store.rename_card(self.db, "owner@example.com", "bad-id", "New name")
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            store.rename_card(self.db, "other@example.com", card["id"], "New name")
        self.assertEqual(self.db.rows, original)
        store.remove_card(self.db, "owner@example.com", card["id"])
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            store.rename_card(self.db, "owner@example.com", card["id"], "New name")
        self.assertEqual(store.load_cards(self.db, "owner@example.com"), [])

    def test_cards_survive_reload_remain_creator_owned_and_enforce_limit_against_saved_state(self):
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

    def test_teammate_card_can_be_removed_and_survives_own_card_creation(self):
        team = store.add_card(self.db, "other@example.com", "Team monitoring", post_url())[0]
        post = store.card_posts(team)[0]
        store.save_remark(self.db, "other@example.com", team["id"], post["id"], "Monitor this discussion")
        store.save_run(self.db, "other@example.com", team["id"], {
            "id": "a" * 64, "at": "2026-11-07T01:00:00+00:00", "metric": "reactions_comments",
            "results": [{"post_id": post["id"], "reactions": 200, "comments": 50, "engagement": 250, "outcome": "ok"}],
        })
        with patch("modules.auth.allowed_emails", return_value={"owner@example.com", "other@example.com"}):
            shared = store.load_workspace_cards(self.db, "owner@example.com")[0]
            app = self.app()
            app.button(key=f"{self.key}_open_{shared['id']}").click().run()
            self.assertFalse(list(app.exception))
            self.assertTrue(any(">250</td>" in element.proto.body for element in app.get("html")))
            self.assertTrue(any("Monitor this discussion" in element.proto.body for element in app.get("html")))
            self.assertFalse({"Run", "Save name", "Save remark", "Add post", "Remove post"}
                             & {button.label for button in app.button})
            self.assertTrue(any(button.label == "Remove card" for button in app.button))
            app.button(key=f"{self.key}_create").click().run()
            self.assertFalse(list(app.exception))
            self.assertIn(f"{self.key}_open_{shared['id']}", [button.key for button in app.button])
            self.assertEqual(len(store.load_cards(self.db, "owner@example.com")), 1)
            store.rename_card(self.db, "other@example.com", team["id"], "Updated team card")
            app.button(key=f"{self.key}_refresh").click().run()
            self.assertEqual(app.button(key=f"{self.key}_open_{shared['id']}").label, "Open Updated team card")
            app.button(key=f"{self.key}_remove_{shared['id']}").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(store.load_cards(self.db, "other@example.com"), [])
            self.assertEqual(len(store.load_cards(self.db, "owner@example.com")), 1)
            self.assertNotIn(f"{self.key}_open_{shared['id']}", [button.key for button in app.button])
            self.assertTrue(any("/runs/" in path for path in self.db.rows))

    def test_remark_editor_saves_reloads_and_retries_without_losing_text(self):
        card = store.add_card(self.db, "owner@example.com", url=post_url())[0]
        card = store.add_post(self.db, "owner@example.com", card["id"], post_url(2))[0]
        first, second = store.card_posts(card)
        card_key = f"{self.key}_{card['id']}"
        app = self.app()
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        value = "Review <script>nothing executable</script>\nFollow up tomorrow"
        app.button(key=f"{card_key}_edit_remark").click().run()
        app.text_area(key=f"{card_key}_remark_{first['id']}").set_value(value)
        self.db.fail_writes = True
        next(button for button in app.button if button.label == "Save remark").click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any("could not be saved" in error.value for error in app.error))
        self.assertEqual(app.text_area(key=f"{card_key}_remark_{first['id']}").value, value)
        self.assertNotIn("remark", store.card_posts(store.load_cards(self.db, "owner@example.com")[0])[0])
        self.db.fail_writes = False
        next(button for button in app.button if button.label == "Save remark").click().run()
        self.assertFalse(list(app.exception))
        tables = [element.proto.body for element in app.get("html") if 'aria-label="Velocity Tracker"' in element.proto.body]
        self.assertIn('class="crt-remark">Remark</th>', tables[0])
        self.assertIn("&lt;script&gt;nothing executable&lt;/script&gt;", tables[0])
        self.assertNotIn("<script>", tables[0])
        app.selectbox(key=f"{card_key}_remark_post").select(second["id"]).run()
        app.button(key=f"{card_key}_edit_remark").click().run()
        self.assertEqual(app.text_area(key=f"{card_key}_remark_{second['id']}").value, "")
        fresh = self.app()
        fresh.button(key=f"{self.key}_open_{card['id']}").click().run()
        fresh.button(key=f"{card_key}_edit_remark").click().run()
        self.assertEqual(fresh.text_area(key=f"{card_key}_remark_{first['id']}").value, value)
        fresh.text_area(key=f"{card_key}_remark_{first['id']}").set_value("")
        next(button for button in fresh.button if button.label == "Save remark").click().run()
        self.assertEqual(store.card_posts(store.load_cards(self.db, "owner@example.com")[0])[0]["remark"], "")

    def test_teammate_cards_do_not_use_own_limit_and_are_hidden_after_allowlist_removal(self):
        for index in range(10):
            store.add_card(self.db, "other@example.com", f"Team card {index}")
        with patch("modules.auth.allowed_emails", return_value={"owner@example.com", "other@example.com"}) as allowed:
            app = self.app()
            self.assertFalse(app.button(key=f"{self.key}_create").disabled)
            self.assertEqual(len([button for button in app.button if button.label.startswith("Open Team card")]), 10)
            allowed.return_value = {"owner@example.com"}
            app.run()
            self.assertFalse(list(app.exception))
            self.assertFalse(any(button.label.startswith("Open Team card") for button in app.button))

    def test_rename_updates_cover_and_survives_a_new_session(self):
        card = store.add_card(self.db, "owner@example.com", url=post_url())[0]
        app = self.app()
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        app.text_input(key=f"{self.key}_{card['id']}_name").set_value("Launch monitoring")
        next(button for button in app.button if button.label == "Save name").click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any(message.value == "Card renamed." for message in app.success))
        self.assertEqual(app.button(key=f"{self.key}_open_{card['id']}").label, "Open Launch monitoring")
        self.assertTrue(any(title.value == "Launch monitoring" for title in app.subheader))
        saved = store.load_cards(self.db, "owner@example.com")[0]
        self.assertEqual(saved, {**card, "name": "Launch monitoring"})
        fresh = self.app()
        self.assertEqual(fresh.button(key=f"{self.key}_open_{card['id']}").label, "Open Launch monitoring")

    def test_failed_rename_keeps_saved_name_and_allows_retry(self):
        card = store.add_card(self.db, "owner@example.com", "Original")[0]
        app = self.app()
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        app.text_input(key=f"{self.key}_{card['id']}_name").set_value("New name")
        self.db.fail_writes = True
        next(button for button in app.button if button.label == "Save name").click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any("could not be renamed" in error.value for error in app.error))
        self.assertEqual(store.load_cards(self.db, "owner@example.com")[0]["name"], "Original")
        self.assertEqual(app.button(key=f"{self.key}_open_{card['id']}").label, "Open Original")
        self.db.fail_writes = False
        next(button for button in app.button if button.label == "Save name").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(store.load_cards(self.db, "owner@example.com")[0]["name"], "New name")

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
                "id": str(index) * 64, "at": f"2026-11-0{index + 1}", "metric": "reactions_comments",
                "results": [{"post_id": first_post["id"], "reactions": views - 10, "comments": 10, "engagement": views, "outcome": "ok"}],
            })
        fresh.button(key=f"{self.key}_refresh").click().run()
        self.assertTrue(any("alive</span>" in element.proto.body for element in fresh.get("html")))
        fresh.button(key=f"{self.key}_{card['id']}_remove_post").click().run()
        self.assertEqual(len(store.card_posts(store.load_cards(self.db, "owner@example.com")[0])), 1)
        self.assertIn(f"{_get_collection_name()}/post", self.db.rows)
        fresh.button(key=f"{self.key}_remove_{card['id']}").click().run()
        self.assertEqual(len(store.load_cards(self.db, "owner@example.com")), 1)

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
