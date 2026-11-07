"""Durable card runs, views-only tables, and background failure recovery."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules import velocity_jobs as jobs, velocity_tracking as store
from modules.facebook_session import FacebookLoginError
from modules.saved_links import library_owner_id
from modules.velocity_table import velocity_card_sheet, view_status
from tests import test_velocity_tracking as fixtures

VelocityTestCase = fixtures.VelocityTestCase
post_url = fixtures.post_url


EMAIL = "owner@example.com"


def run_for(card, index=1, views=100):
    return {"id": f"{index:064x}", "at": f"2026-11-07T01:{index:02}:00+00:00", "results": [
        {"post_id": post["id"], "url": post["url"], "views": views, "outcome": "ok"}
        for post in store.card_posts(card)]}


class RunStoreTests(VelocityTestCase):
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

    def test_missing_views_keep_run_columns_and_never_fall_back_to_comments(self):
        card = store.add_card(self.db, EMAIL, url=post_url())[0]
        card = store.add_post(self.db, EMAIL, card["id"], post_url(2))[0]
        runs = [run_for(card, index, value) for index, value in enumerate((100, None, 300), 1)]
        runs[1]["results"][0].update(comments=999, outcome="unavailable", reason="No public views")
        runs[1]["results"].pop()  # Second row was not in that run.
        card["runs"] = runs
        sheet = velocity_card_sheet(card)
        self.assertIn('>100</td><td title="No public views', sheet)
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
                self.assertEqual(view_status(values), status)


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
        with patch("modules.velocity_jobs.collect_views", new=AsyncMock(return_value=results)) as scrape:
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
        with patch("modules.velocity_jobs.collect_views", new=AsyncMock(side_effect=FacebookLoginError("Reconnect Facebook"))):
            job = jobs.start_run(EMAIL, self.card["id"], hosted=True)
        self.assertEqual(job.status, "error")
        self.assertIn("Reconnect Facebook", job.error)
        self.assertNotIn("runs", store.load_cards(self.db, EMAIL)[0])

    def test_partial_failure_saves_counts_and_marks_remaining_post_failed(self):
        store.add_post(self.db, EMAIL, self.card["id"], post_url(2))

        async def stopped(email, posts, *, partial, **kwargs):
            partial(run_for(self.card)["results"])
            raise FacebookLoginError("Reconnect Facebook")

        with patch("modules.velocity_jobs.collect_views", side_effect=stopped):
            job = jobs.start_run(EMAIL, self.card["id"], hosted=True)
        self.assertEqual(job.status, "complete")
        rows = store.load_cards(self.db, EMAIL)[0]["runs"][0]["results"]
        self.assertEqual([row["views"] for row in rows], [100, None])
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
