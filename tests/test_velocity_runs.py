"""Durable card runs, likes + comments tables, and background failure recovery."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules import velocity_jobs as jobs, velocity_tracking as store
from modules.facebook_session import FacebookLoginError
from modules.saved_links import library_owner_id
from modules.velocity_metrics import METRIC, engagement_count
from modules.velocity_table import velocity_card_sheet, engagement_status, engagement_change_percent
from tests import test_velocity_tracking as fixtures

VelocityTestCase = fixtures.VelocityTestCase
post_url = fixtures.post_url


EMAIL = "owner@example.com"


def run_for(card, index=1, total=100):
    return {"id": f"{index:064x}", "at": f"2026-11-07T01:{index:02}:00+00:00", "metric": METRIC, "results": [
        {"post_id": post["id"], "url": post["url"], "reactions": total, "comments": 0 if total is not None else None, "engagement": total, "outcome": "ok"}
        for post in store.card_posts(card)]}


class RunStoreTests(VelocityTestCase):
    def test_old_views_are_preserved_but_never_compared_with_engagement(self):
        card = store.add_card(self.db, EMAIL, url=post_url())[0]
        old = run_for(card)
        old.pop("metric")
        old["results"] = [{"post_id": store.card_posts(card)[0]["id"], "views": 10000, "outcome": "ok"}]
        for run in [old, run_for(card, 2, 100)]:
            store.save_run(self.db, EMAIL, card["id"], run)
        saved = store.load_cards(self.db, EMAIL)[0]
        sheet = velocity_card_sheet(saved)
        self.assertIn("10,000 views", sheet)
        self.assertIn("Earlier views check", sheet)
        self.assertNotIn('class="crt-status ', sheet)
        self.assertNotIn("-99.0%", sheet)
        store.save_run(self.db, EMAIL, card["id"], run_for(card, 3, 125))
        self.assertIn(">+25.0%</td>", velocity_card_sheet(store.load_cards(self.db, EMAIL)[0]))
        self.assertEqual(saved["runs"][0]["results"][0]["views"], 10000)

    def test_total_requires_both_valid_counts_and_is_their_sum(self):
        self.assertEqual(engagement_count({"reactions": 120, "comments": 30, "outcome": "ok"}), 150)
        self.assertEqual(engagement_count({"reactions": 0, "comments": 0, "outcome": "ok"}), 0)
        for row in ({"reactions": 12}, {"comments": 12}, {"views": 120},
                    {"reactions": True, "comments": 0}, {"reactions": -1, "comments": 2}):
            self.assertIsNone(engagement_count({**row, "outcome": "ok"}))

    def test_rename_preserves_posts_and_runs_including_later_run_saves(self):
        first = store.add_card(self.db, EMAIL, "First", post_url())[0]
        other = store.add_card(self.db, EMAIL, "Other")[1]
        store.save_run(self.db, EMAIL, first["id"], run_for(first))
        before = store.load_cards(self.db, EMAIL)[0]
        renamed = store.rename_card(self.db, EMAIL, first["id"], "  Launch   monitoring  ")
        self.assertEqual(renamed, [{**before, "name": "Launch monitoring"}, other])
        store.save_run(self.db, EMAIL, first["id"], run_for(first, 2, 200))
        saved = store.load_cards(self.db, EMAIL)[0]
        self.assertEqual(saved["name"], "Launch monitoring")
        self.assertEqual(saved["run_count"], 2)
        self.assertEqual(store.card_posts(saved), store.card_posts(first))
        self.assertEqual(len([key for key in self.db.rows if "/runs/" in key]), 2)

    def test_runs_are_per_card_idempotent_and_keep_all_archives(self):
        first = store.add_card(self.db, EMAIL, "First", post_url())[0]
        second = store.add_card(self.db, EMAIL, "Second", post_url())[1]
        store.add_post(self.db, EMAIL, first["id"], post_url(2))
        for index in range(1, 11):
            run = run_for(first, index, index * 100)
            store.save_run(self.db, EMAIL, first["id"], run)
            store.save_run(self.db, EMAIL, first["id"], run)
        saved, other = store.load_cards(self.db, EMAIL)
        self.assertEqual(saved["run_count"], 10)
        self.assertEqual([row["number"] for row in saved["runs"]], list(range(3, 11)))
        self.assertEqual(len(store.card_posts(saved)), 2)  # Concurrent row edit survived.
        self.assertNotIn("url", saved["runs"][0]["results"][0])
        self.assertEqual(other, second)
        self.assertEqual(store.load_cards(self.db, "someone@example.com"), [])
        self.assertEqual(len([key for key in self.db.rows if "/runs/" in key]), 10)
        sheet = velocity_card_sheet(saved)
        self.assertIn("Run 10", sheet)
        self.assertIn("Showing the latest 8 runs", sheet)

    def test_finishing_after_card_removal_archives_without_restoring_card(self):
        card = store.add_card(self.db, EMAIL, url=post_url())[0]
        store.remove_card(self.db, EMAIL, card["id"])
        store.save_run(self.db, EMAIL, card["id"], run_for(card))
        self.assertEqual(store.load_cards(self.db, EMAIL), [])
        self.assertEqual(len([key for key in self.db.rows if "/runs/" in key]), 1)

    def test_missing_reactions_keep_run_columns_and_never_use_comments_alone(self):
        card = store.add_card(self.db, EMAIL, url=post_url())[0]
        card = store.add_post(self.db, EMAIL, card["id"], post_url(2))[0]
        runs = [run_for(card, index, value) for index, value in enumerate((100, None, 300), 1)]
        runs[1]["results"][0].update(comments=999, outcome="unavailable", reason="Missing reaction total")
        runs[1]["results"].pop()  # Second row was not in that run.
        card["runs"] = runs
        sheet = velocity_card_sheet(card)
        self.assertIn('>100</td><td title="Missing reaction total', sheet)
        self.assertIn('>Unavailable</td><td', sheet)
        self.assertIn('>300</td>', sheet)
        self.assertNotIn("999", sheet)
        self.assertNotIn('class="crt-status ', sheet)
        self.assertIn("Post was not included in this run", sheet)

    def test_status_uses_consecutive_counts_and_handles_real_zero(self):
        for values, status in (([100], None), ([100, 200], "alive"), ([100, 200, 220], "dying"),
                               ([100, 100, 100], "dead"), ([0, 0], "dead"), ([0, 10], "alive"),
                               ([100, None], None), ([100, None, 200], None), ([None, 200, 300], "alive")):
            with self.subTest(values=values):
                self.assertEqual(engagement_status(values), status)

    def test_percentage_uses_latest_pair_and_never_skips_missing_counts(self):
        for values, expected in (([], None), ([100], None), ([100, 150], 50), ([200, 150], -25),
                                 ([100, 100], 0), ([100, 0], -100), ([0, 100], None), ([0, 0], None),
                                 ([100, None], None), ([100, None, 200], None),
                                 ([100, 200, 250], 25), ([None, 200, 300], 50)):
            with self.subTest(values=values):
                self.assertEqual(engagement_change_percent(values), expected)

    def test_headers_show_run_times_and_change_column_follows_status(self):
        card = store.add_card(self.db, EMAIL, url=post_url())[0]
        store.add_post(self.db, EMAIL, card["id"], post_url(2))
        for index, views in enumerate((100, 125), 1):
            store.save_run(self.db, EMAIL, card["id"], run_for(card, index, views))
        sheet = velocity_card_sheet(store.load_cards(self.db, EMAIL)[0])
        self.assertIn('class="crt-run-date">07 Nov 2026</span>', sheet)
        self.assertIn('class="crt-run-time">01:01 UTC</span>', sheet)
        self.assertIn('class="crt-run-time">01:02 UTC</span>', sheet)
        self.assertIn('>Status</th><th scope=col title="Latest run versus the previous run">Change (%)</th>', sheet)
        self.assertIn('>+25.0%</td>', sheet)
        self.assertIn('title="Needs likes + comments totals from both the latest and previous runs.">—</td>', sheet)
        self.assertNotIn('>Check 1</th>', sheet)
        empty = velocity_card_sheet({"posts": []})
        self.assertIn('colspan="12"', empty)
        self.assertIn('class="crt-remark">Remark</th>', empty)
        self.assertEqual(empty.count('title="Not run yet"'), 8)

    def test_dates_normalize_to_utc_and_growth_from_zero_is_not_infinite(self):
        card = store.add_card(self.db, EMAIL, url=post_url())[0]
        card["runs"] = [run_for(card, 1, 0), run_for(card, 2, 10)]
        card["runs"][1]["at"] = "2026-11-08T00:02:00+08:00"
        sheet = velocity_card_sheet(card)
        self.assertIn('class="crt-run-date">07 Nov 2026</span>', sheet)
        self.assertIn('class="crt-run-time">16:02 UTC</span>', sheet)
        self.assertIn('title="Percentage change is undefined because the previous total is zero.">—</td>', sheet)


def inline_thread(*, target, args=(), **kwargs):
    return SimpleNamespace(start=lambda: target(*args))


class RunWorkerTests(VelocityTestCase):
    def setUp(self):
        super().setUp()
        jobs._jobs.clear()
        self.addCleanup(jobs._jobs.clear)
        self.card = store.add_card(self.db, EMAIL, url=post_url())[0]
        for target, value in (("modules.velocity_jobs.database.init_db", self.db),
                              ("modules.velocity_jobs.active_collection", None)):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        thread = patch("modules.velocity_jobs.threading.Thread", side_effect=inline_thread)
        thread.start()
        self.addCleanup(thread.stop)

    def test_success_persists_once_and_save_retry_does_not_scrape_again(self):
        self.db.fail_writes = True
        results = run_for(self.card)["results"]
        with patch("modules.velocity_jobs.collect_engagement", new=AsyncMock(return_value=results)) as scrape:
            job = jobs.start_run(EMAIL, self.card["id"], hosted=True)
            self.assertEqual(job.status, "save_failed")
            self.assertEqual(store.load_cards(self.db, EMAIL)[0].get("runs", []), [])
            with self.assertRaisesRegex(ValueError, "Finish the current"):
                jobs.start_run(EMAIL, self.card["id"], hosted=True)
            self.db.fail_writes = False
            jobs.retry_save("someone@example.com", job.id)
            self.assertEqual(job.status, "save_failed")
            jobs.retry_save(EMAIL, job.id)
            jobs.retry_save(EMAIL, job.id)
            self.assertEqual(job.status, "complete")
            self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 1)
            self.assertEqual(scrape.await_count, 1)

    def test_auth_failure_does_not_fabricate_check(self):
        with patch("modules.velocity_jobs.collect_engagement", new=AsyncMock(side_effect=FacebookLoginError("Reconnect Facebook"))):
            job = jobs.start_run(EMAIL, self.card["id"], hosted=True)
        self.assertEqual(job.status, "error")
        self.assertIn("Reconnect Facebook", job.error)
        self.assertNotIn("runs", store.load_cards(self.db, EMAIL)[0])

    def test_partial_failure_saves_counts_and_marks_remaining_post_failed(self):
        store.add_post(self.db, EMAIL, self.card["id"], post_url(2))

        async def stopped(email, posts, *, partial, **kwargs):
            partial(run_for(self.card)["results"])
            raise FacebookLoginError("Reconnect Facebook")

        with patch("modules.velocity_jobs.collect_engagement", side_effect=stopped):
            job = jobs.start_run(EMAIL, self.card["id"], hosted=True)
        self.assertEqual(job.status, "complete")
        rows = store.load_cards(self.db, EMAIL)[0]["runs"][0]["results"]
        self.assertEqual([row["engagement"] for row in rows], [100, None])
        self.assertEqual(rows[1]["outcome"], "error")
        self.assertIn("Completed counts were saved", job.run["warning"])

    def test_empty_cards_and_active_collections_cannot_start_a_run(self):
        empty = store.add_card(self.db, EMAIL)[1]
        with self.assertRaisesRegex(ValueError, "Add a Facebook post"):
            jobs.start_run(EMAIL, empty["id"], hosted=True)
        with patch("modules.velocity_jobs.active_collection", return_value=object()), \
                self.assertRaisesRegex(ValueError, "current Facebook collection"):
            jobs.start_run(EMAIL, self.card["id"], hosted=True)


class RunUITests(VelocityTestCase):
    script = fixtures.VelocityUITests.script
    key = fixtures.VelocityUITests.key
    app = fixtures.VelocityUITests.app

    def setUp(self):
        super().setUp()
        jobs._jobs.clear()
        self.addCleanup(jobs._jobs.clear)
        database = patch("modules.velocity_tracking_ui.database.init_db", return_value=self.db)
        database.start()
        self.addCleanup(database.stop)
        for target, value in (("modules.auth.verify_access", EMAIL), ("modules.auth.allowed_emails", {EMAIL})):
            auth = patch(target, return_value=value)
            auth.start()
            self.addCleanup(auth.stop)

    def test_saved_unavailable_reason_is_visible_without_hovering(self):
        card = store.add_card(self.db, EMAIL, url=post_url())[0]
        run = run_for(card, total=None)
        run["results"][0].update(outcome="unavailable", reason="The requested post could not be identified on the page.")
        store.save_run(self.db, EMAIL, card["id"], run)
        app = self.app()
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertFalse(list(app.exception))
        self.assertIn("Why counts are unavailable", [expander.label for expander in app.expander])
        self.assertTrue(any("The requested post could not be identified" in element.value for element in app.markdown))
        self.assertTrue(any("two consecutive likes + comments totals" in element.value for element in app.caption))

    def test_active_run_disables_duplicate_runs_and_edits_while_polling(self):
        card = store.add_card(self.db, EMAIL, url=post_url())[0]
        run = run_for(card)
        job = jobs.VelocityJob(run["id"], card["id"], store.card_posts(card))
        jobs._jobs[library_owner_id(EMAIL)] = job
        with patch("modules.auth.verify_access", return_value=EMAIL), patch("modules.auth.allowed_emails", return_value={EMAIL}):
            app = self.app()
            app.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertTrue(app.button(key=f"{self.key}_{card['id']}_run").disabled)
        self.assertTrue(app.button(key=f"{self.key}_remove_{card['id']}").disabled)
        self.assertEqual(len(app.get("progress")), 1)
        self.assertNotIn("runs", store.load_cards(self.db, EMAIL)[0])
        self.assertTrue(any('class="sm-running"' in element.proto.body for element in app.get("html")))
        job.status = "saving"
        app.run()
        self.assertTrue(any('class="sm-running"' in element.proto.body for element in app.get("html")))
        app.button(key=f"{self.key}_close").click().run()
        self.assertTrue(any('class="sm-running"' in element.proto.body for element in app.get("html")))
        job.status = "error"
        job.error = "Collection stopped."
        app.run()
        self.assertFalse(list(app.exception))
        self.assertFalse(any('class="sm-running"' in element.proto.body for element in app.get("html")))
        self.assertEqual(len(app.get("progress")), 0)

    def test_run_is_disabled_without_links_and_saves_a_check_on_click(self):
        card = store.add_card(self.db, EMAIL)[0]
        app = self.app()
        button_key = f"{self.key}_{card['id']}_run"
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertTrue(app.button(key=button_key).disabled)
        card = store.add_post(self.db, EMAIL, card["id"], post_url())[0]
        app.button(key=f"{self.key}_refresh").click().run()
        self.assertFalse(app.button(key=button_key).disabled)

        def finish(email, card_id, **kwargs):
            run = run_for(card)
            store.save_run(self.db, email, card_id, run)
            job = jobs.VelocityJob(run["id"], card_id, store.card_posts(card), status="complete", run=run)
            jobs._jobs[library_owner_id(email)] = job
            return job

        with patch("modules.velocity_jobs.start_run", side_effect=finish) as start:
            app.button(key=button_key).click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(start.call_count, 1)
        self.assertTrue(any(">100</td>" in element.proto.body for element in app.get("html")))
        self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 1)
        app.run()
        self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 1)
        fresh = self.app()
        fresh.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertTrue(any(">100</td>" in element.proto.body for element in fresh.get("html")))
        fresh.button(key=f"{self.key}_close").click().run()
        self.assertFalse(any('aria-label="Velocity Tracker"' in element.proto.body for element in fresh.get("html")))
        fresh.button(key=f"{self.key}_refresh").click().run()
        self.assertFalse(any(button.label == "Close notepad" for button in fresh.button))


if __name__ == "__main__":
    unittest.main()
