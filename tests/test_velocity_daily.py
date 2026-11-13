"""Unattended daily velocity checks: scheduling, recovery and surfaced failures."""

from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
import os
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from modules import velocity_jobs as jobs, velocity_schedule as schedule, velocity_tracking as store
from modules.saved_links import library_owner_id
from tests import test_velocity_tracking as fixtures

VelocityTestCase = fixtures.VelocityTestCase
post_url = fixtures.post_url

EMAIL = "owner@example.com"
OWNER = library_owner_id(EMAIL)
SETTINGS = {"VELOCITY_DAILY_CHECKS": "1", "VELOCITY_DAILY_HOUR": "2",
            "VELOCITY_DAILY_TIMEZONE": "UTC", "AI_PROVIDER": "gemini"}


def at(hour, minute=0, day=8):
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def results_for(card, total=100):
    return [{"post_id": post["id"], "url": post["url"], "reactions": total, "comments": 0,
             "engagement": total, "outcome": "ok"} for post in store.card_posts(card)]


class ScheduleTestCase(VelocityTestCase):
    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, SETTINGS)
        settings.start()
        self.addCleanup(settings.stop)


class DueCardTests(ScheduleTestCase):
    def setUp(self):
        super().setUp()
        self.card = store.add_card(self.db, EMAIL, "Launch", post_url())[0]
        self.empty = store.add_card(self.db, EMAIL, "Empty")[1]
        self.cards = [self.card, self.empty]

    def test_only_cards_with_posts_are_due_and_only_after_the_scheduled_hour(self):
        self.assertEqual(schedule.due_cards({}, self.cards, at(1, 59)), [])
        self.assertEqual([card["id"] for card in schedule.due_cards({}, self.cards, at(2))], [self.card["id"]])
        self.assertEqual([card["id"] for card in schedule.due_cards({}, self.cards, at(23, 30))], [self.card["id"]])

    def test_a_saved_check_is_not_repeated_until_the_next_scheduled_day(self):
        records = {self.card["id"]: {"date": "2026-11-08", "status": "complete", "attempts": 1,
                                     "attempted_at": at(2).timestamp()}}
        self.assertEqual(schedule.due_cards(records, self.cards, at(14)), [])
        self.assertEqual([card["id"] for card in schedule.due_cards(records, self.cards, at(2, 0, 9))],
                         [self.card["id"]])

    def test_failures_retry_after_a_gap_and_stop_at_the_attempt_limit(self):
        records = {self.card["id"]: {"date": "2026-11-08", "status": "error", "attempts": 1,
                                     "attempted_at": at(2).timestamp(), "error": "Facebook is not connected."}}
        self.assertEqual(schedule.due_cards(records, self.cards, at(2, 20)), [])
        self.assertEqual(len(schedule.due_cards(records, self.cards, at(2, 31))), 1)
        records[self.card["id"]]["attempts"] = schedule.MAX_ATTEMPTS
        self.assertEqual(schedule.due_cards(records, self.cards, at(23)), [])

    def test_a_teammates_shared_card_is_never_run_from_another_account(self):
        shared = {**self.card, "shared": True, "owner_id": "someone-else", "source_card_id": self.card["id"]}
        self.assertEqual(schedule.due_cards({}, [shared], at(2)), [])

    def test_the_configured_hour_timezone_and_switch_are_read_from_settings(self):
        with patch.dict(os.environ, {"VELOCITY_DAILY_HOUR": "17", "VELOCITY_DAILY_TIMEZONE": "Asia/Manila"}):
            self.assertEqual(schedule.schedule_hour(), 17)
            self.assertEqual(schedule.zone_label(), "Asia/Manila")
            self.assertEqual(schedule.due_time(at(3)).hour, 17)
            self.assertEqual(schedule.next_run_at(at(3)).strftime("%d %H:%M"), "08 17:00")
            self.assertEqual(schedule.next_run_at(at(18)).strftime("%d %H:%M"), "09 17:00")
        for invalid in ("24", "-1", "noon", ""):
            with patch.dict(os.environ, {"VELOCITY_DAILY_HOUR": invalid}):
                self.assertEqual(schedule.schedule_hour(), schedule.DEFAULT_HOUR)
        with patch.dict(os.environ, {"VELOCITY_DAILY_TIMEZONE": "Mars/Olympus"}):
            self.assertEqual(schedule.zone_label(), "UTC")
        self.assertIn("02:00 UTC", schedule.schedule_caption(at(1)))
        with patch.dict(os.environ, {"VELOCITY_DAILY_CHECKS": "0"}):
            self.assertFalse(schedule.enabled())
            self.assertIn("turned off", schedule.schedule_caption(at(1)))
            self.assertEqual(schedule.tick(moment=at(2)), {"ran": 0, "failed": 0, "deferred": 0})


class RunDueFixture(ScheduleTestCase):
    """One owned card, a connected hosted login, and a patched scraper."""

    def setUp(self):
        super().setUp()
        jobs._jobs.clear()
        self.addCleanup(jobs._jobs.clear)
        self.card = store.add_card(self.db, EMAIL, "Launch", post_url())[0]
        # A saved hosted login needs no fresh verification to run unattended.
        for target, value in (("modules.velocity_jobs.database.init_db", self.db),
                              ("modules.velocity_jobs.active_collection", None),
                              ("modules.facebook_remote.remote_facebook_configured", True),
                              ("modules.facebook_remote.remote_facebook_session",
                               SimpleNamespace(status="saved", user_id="7", name="Owner"))):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def scraper(self, total=100):
        return patch("modules.velocity_jobs.collect_engagement",
                     new=AsyncMock(return_value=results_for(self.card, total)))


class RunDueTests(RunDueFixture):
    def test_each_card_is_checked_once_a_day_without_any_dashboard_session(self):
        with self.scraper() as scrape:
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2)),
                             {"ran": 1, "failed": 0, "deferred": 0})
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(9)),
                             {"ran": 0, "failed": 0, "deferred": 0})
            self.assertEqual(scrape.await_count, 1)
        saved = store.load_cards(self.db, EMAIL)[0]
        self.assertEqual(saved["run_count"], 1)
        self.assertEqual(saved["runs"][0]["results"][0]["engagement"], 100)
        self.assertEqual(saved["runs"][0]["trigger"], "daily")
        record = schedule.load_state(self.db, OWNER)[self.card["id"]]
        self.assertEqual((record["date"], record["status"], record["attempts"]), ("2026-11-08", "complete", 1))
        with self.scraper(175) as scrape:
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 0, 9))["ran"], 1)
            self.assertEqual(scrape.await_count, 1)
        self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 2)

    def test_every_card_of_an_account_runs_in_the_same_pass(self):
        second = store.add_card(self.db, EMAIL, "Second", post_url(2))[1]
        store.add_card(self.db, EMAIL, "No posts")
        with patch("modules.velocity_jobs.collect_engagement", new=AsyncMock(side_effect=lambda email, posts, **kwargs: [
                {"post_id": post["id"], "url": post["url"], "reactions": 10, "comments": 1,
                 "engagement": 11, "outcome": "ok"} for post in posts])):
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))["ran"], 2)
        self.assertEqual([card.get("run_count", 0) for card in store.load_cards(self.db, EMAIL)], [1, 1, 0])
        self.assertEqual(sorted(schedule.load_state(self.db, OWNER)), sorted([self.card["id"], second["id"]]))

    def test_a_manual_run_in_progress_defers_rather_than_scraping_twice(self):
        jobs._jobs[OWNER] = jobs.VelocityJob("f" * 64, self.card["id"], store.card_posts(self.card),
                                             status="running")
        with self.scraper() as scrape:
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2)),
                             {"ran": 0, "failed": 0, "deferred": 1})
            self.assertEqual(scrape.await_count, 0)
            self.assertEqual(schedule.load_state(self.db, OWNER), {})
            jobs._jobs.clear()
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 10))["ran"], 1)
        self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 1)

    def test_a_running_collection_defers_the_daily_check(self):
        with patch("modules.velocity_jobs.active_collection", return_value=object()), self.scraper() as scrape:
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))["deferred"], 1)
            self.assertEqual(scrape.await_count, 0)

    def test_a_disconnected_hosted_login_is_recorded_without_spending_browser_time(self):
        with patch("modules.facebook_remote.remote_facebook_configured", return_value=True), \
                patch("modules.facebook_remote.remote_facebook_session",
                      return_value=SimpleNamespace(status="disconnected", user_id="", name="")), \
                self.scraper() as scrape:
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))["failed"], 1)
            self.assertEqual(scrape.await_count, 0)
        record = schedule.load_state(self.db, OWNER)[self.card["id"]]
        self.assertEqual(record["status"], "error")
        self.assertIn("connect Facebook", record["error"])
        self.assertNotIn("runs", store.load_cards(self.db, EMAIL)[0])
        card = schedule.attach_status(self.db, EMAIL, store.load_cards(self.db, EMAIL))[0]
        self.assertIn("connect Facebook", schedule.daily_notice(card))

    def test_a_reconnected_login_resumes_the_same_day_without_waiting(self):
        disconnected = patch("modules.facebook_remote.remote_facebook_session",
                             return_value=SimpleNamespace(status="disconnected", user_id="", name=""))
        with disconnected, self.scraper():
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))["failed"], 1)
            written = schedule.load_state(self.db, OWNER)[self.card["id"]]
            self.assertTrue(written["blocked"])
            # A blocked card is reported once, not rewritten on every five-minute tick.
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 5))["failed"], 1)
            self.assertEqual(schedule.load_state(self.db, OWNER)[self.card["id"]], written)
        with self.scraper() as scrape:
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 10))["ran"], 1)
            self.assertEqual(scrape.await_count, 1)
        record = schedule.load_state(self.db, OWNER)[self.card["id"]]
        self.assertEqual(record["status"], "complete")
        self.assertNotIn("blocked", record)
        self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 1)

    def test_a_login_failure_is_recorded_and_retried_later_the_same_day(self):
        from modules.facebook_session import FacebookLoginError

        with patch("modules.velocity_jobs.collect_engagement",
                   new=AsyncMock(side_effect=FacebookLoginError("Reconnect Facebook"))):
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))["failed"], 1)
        record = schedule.load_state(self.db, OWNER)[self.card["id"]]
        self.assertEqual((record["status"], record["attempts"]), ("error", 1))
        self.assertIn("Reconnect Facebook", record["error"])
        self.assertNotIn("runs", store.load_cards(self.db, EMAIL)[0])
        with self.scraper() as scrape:
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 20))["ran"], 0)
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 40))["ran"], 1)
            self.assertEqual(scrape.await_count, 1)
        self.assertEqual(schedule.load_state(self.db, OWNER)[self.card["id"]]["attempts"], 2)

    def test_a_failed_save_is_retried_and_never_blocks_the_next_check(self):
        self.db.fail_writes = True
        with self.scraper() as scrape:
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))["failed"], 1)
            self.assertEqual(jobs.latest_job(EMAIL).status, "save_failed")
            self.db.fail_writes = False
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 40))["ran"], 1)
            self.assertEqual(scrape.await_count, 2)
        self.assertEqual(jobs.latest_job(EMAIL).status, "complete")
        self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 2)
        self.assertEqual(schedule.load_state(self.db, OWNER)[self.card["id"]]["status"], "complete")

    def test_records_of_removed_cards_are_dropped(self):
        schedule._write_state(self.db, OWNER, {"deleted": {"date": "2026-11-07", "status": "complete"}})
        with self.scraper():
            schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))
        self.assertEqual(list(schedule.load_state(self.db, OWNER)), [self.card["id"]])

    def test_an_unreadable_schedule_never_hides_a_card(self):
        self.db.fail_reads = True
        self.assertEqual(schedule.attach_status(self.db, EMAIL, [self.card]), [self.card])


class TickTests(ScheduleTestCase):
    def test_no_approved_account_means_no_firestore_work(self):
        with patch("modules.auth.allowed_emails", return_value=set()), \
                patch("modules.velocity_schedule.database.init_db", side_effect=AssertionError("connected")):
            self.assertEqual(schedule.tick(moment=at(2)), {"ran": 0, "failed": 0, "deferred": 0})

    def test_nothing_is_read_before_the_scheduled_hour(self):
        with patch("modules.auth.allowed_emails", return_value={EMAIL}), \
                patch("modules.velocity_schedule.database.init_db", side_effect=AssertionError("connected")):
            self.assertEqual(schedule.tick(moment=at(1, 59)), {"ran": 0, "failed": 0, "deferred": 0})

    def test_each_owner_runs_its_own_cards_and_one_failure_does_not_stop_the_rest(self):
        seen = []

        def run_due(db, email, *, hosted, moment):
            seen.append(email)
            if email == "broken@example.com":
                raise RuntimeError("Firestore is unavailable")
            return {"ran": 1, "failed": 0, "deferred": 0}

        with patch("modules.auth.allowed_emails", return_value={EMAIL, "broken@example.com"}), \
                patch("modules.velocity_schedule.database.init_db", return_value=self.db), \
                patch("modules.velocity_schedule.run_due", side_effect=run_due):
            self.assertEqual(schedule.tick(moment=at(2)), {"ran": 1, "failed": 0, "deferred": 0})
        self.assertEqual(sorted(seen), ["broken@example.com", EMAIL])


class DailyStatusFixture(ScheduleTestCase):
    """The velocity workspace rendered against the in-memory database."""

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

    def record(self, card, **changes):
        schedule._write_state(self.db, OWNER, {card["id"]: {
            "date": "2026-11-08", "attempts": 1, "attempted_at": at(2).timestamp(), **changes}})


class DailyStatusUITests(DailyStatusFixture):
    def test_a_failed_check_asks_for_attention(self):
        card = store.add_card(self.db, EMAIL, "Launch", post_url())[0]
        self.record(card, status="error", error="Facebook is not connected. Open Scrape Data to reconnect.")
        app = self.app()
        self.assertFalse(any("checked automatically" in element.value for element in app.caption))
        self.assertTrue(any("Daily check failed" in element.proto.body for element in app.get("html")))
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any("Open Scrape Data to reconnect" in element.value for element in app.warning))

    def test_a_saved_daily_check_shows_its_result_without_any_notice(self):
        card = store.add_card(self.db, EMAIL, "Launch", post_url())[0]
        store.save_run(self.db, EMAIL, card["id"], {
            "id": "b" * 64, "at": "2026-11-08T02:03:00+00:00", "metric": "reactions_comments", "trigger": "daily",
            "results": [{"post_id": store.card_posts(card)[0]["id"], "reactions": 90, "comments": 10,
                         "engagement": 100, "outcome": "ok"}]})
        self.record(card, status="complete", run_id="b" * 64)
        app = self.app()
        self.assertFalse(any("Daily check failed" in element.proto.body for element in app.get("html")))
        app.button(key=f"{self.key}_open_{card['id']}").click().run()
        self.assertFalse(list(app.exception))
        self.assertFalse(list(app.warning))
        self.assertTrue(any(">100</td>" in element.proto.body for element in app.get("html")))


class SchedulerThreadTests(ScheduleTestCase):
    def setUp(self):
        super().setUp()
        # Rendering the dashboard in another test may already have started one.
        schedule.stop_scheduler()
        self.addCleanup(schedule.stop_scheduler)

    def test_one_worker_per_process_keeps_ticking_until_it_is_stopped(self):
        ticked = threading.Event()
        with patch.object(schedule, "STARTUP_SECONDS", 0), patch.object(schedule, "TICK_SECONDS", 0.05), \
                patch.object(schedule, "tick", side_effect=lambda **kwargs: (
                    ticked.set(), {"ran": 0, "failed": 0, "deferred": 0})[1]):
            self.assertTrue(schedule.ensure_scheduler_started())
            self.assertFalse(schedule.ensure_scheduler_started())
            self.assertTrue(ticked.wait(5))
            schedule.stop_scheduler()
        self.assertTrue(schedule.enabled())

    def test_the_worker_alone_saves_a_column_with_nobody_signed_in(self):
        card = store.add_card(self.db, EMAIL, "Launch", post_url())[0]
        patches = [
            patch("modules.velocity_schedule.database.init_db", return_value=self.db),
            patch("modules.velocity_jobs.database.init_db", return_value=self.db),
            patch("modules.velocity_jobs.active_collection", return_value=None),
            patch("modules.auth.allowed_emails", return_value={EMAIL}),
            patch("modules.facebook_remote.remote_facebook_configured", return_value=True),
            patch("modules.facebook_remote.remote_facebook_session",
                  return_value=SimpleNamespace(status="saved", user_id="7", name="Owner")),
            patch("modules.velocity_jobs.collect_engagement",
                  new=AsyncMock(return_value=results_for(card, 250))),
            patch.object(schedule, "now_local", return_value=at(2)),
            patch.object(schedule, "STARTUP_SECONDS", 0),
            patch.object(schedule, "TICK_SECONDS", 0.05),
        ]
        with ExitStack() as stack:
            for mock in patches:
                stack.enter_context(mock)
            self.assertTrue(schedule.ensure_scheduler_started())
            deadline = time.monotonic() + 10
            while store.load_cards(self.db, EMAIL)[0].get("run_count", 0) < 1:
                self.assertLess(time.monotonic(), deadline, "the worker saved no check")
                time.sleep(0.05)
            schedule.stop_scheduler()
            saved = store.load_cards(self.db, EMAIL)[0]
            self.assertEqual(saved["runs"][0]["results"][0]["engagement"], 250)
            self.assertEqual(saved["runs"][0]["trigger"], "daily")
            self.assertEqual(schedule.load_state(self.db, OWNER)[card["id"]]["status"], "complete")
            # A later tick on the same scheduled day must not add a second column.
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(5))["ran"], 0)
            self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 1)

    def test_the_switch_keeps_the_worker_from_starting(self):
        with patch.dict(os.environ, {"VELOCITY_DAILY_CHECKS": "off"}):
            self.assertFalse(schedule.ensure_scheduler_started())


if __name__ == "__main__":
    unittest.main()
