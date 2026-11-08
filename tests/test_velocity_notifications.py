"""Subscriptions, message wording and delivery for automatic velocity checks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import os
import unittest

from modules import (velocity_jobs as jobs, velocity_notifications as alerts,
                     velocity_schedule as schedule, velocity_tracking as store)
from modules.saved_links import library_owner_id
from modules.velocity_metrics import METRIC
from tests import test_velocity_daily as daily
from tests import test_velocity_tracking as fixtures

ScheduleTestCase = daily.ScheduleTestCase
at, post_url, results_for = daily.at, fixtures.post_url, daily.results_for
EMAIL = daily.EMAIL
OWNER = daily.OWNER


def run_with(card, totals, *, index=1, metric=METRIC, warning=""):
    """One run holding the given total per post, in card order; ids stay distinct."""
    run = {"id": f"{index:064x}", "at": f"2026-11-08T02:{index:02}:00+00:00", "metric": metric, "results": [
        {"post_id": post["id"], "reactions": total, "comments": 0, "engagement": total,
         "outcome": "ok" if total is not None else "error"}
        for post, total in zip(store.card_posts(card), totals)]}
    return {**run, "warning": warning} if warning else run


class SubscriptionTests(ScheduleTestCase):
    def setUp(self):
        super().setUp()
        self.card = store.add_card(self.db, EMAIL, "Launch", post_url())[0]

    def test_nothing_is_queued_for_an_account_that_did_not_subscribe(self):
        self.assertFalse(alerts.is_subscribed(self.db, EMAIL))
        self.assertFalse(alerts.record_run(self.db, EMAIL, self.card, run=run_with(self.card, [100])))
        self.assertEqual(alerts.take_pending(self.db, EMAIL), [])

    def test_subscribing_queues_each_run_once_and_taking_clears_the_queue(self):
        self.assertTrue(alerts.set_subscription(self.db, EMAIL, True))
        self.assertTrue(alerts.is_subscribed(self.db, EMAIL))
        self.assertTrue(alerts.record_run(self.db, EMAIL, self.card, run=run_with(self.card, [100])))
        self.assertTrue(alerts.record_run(self.db, EMAIL, self.card, error="Facebook is not connected."))
        queued = alerts.take_pending(self.db, EMAIL)
        self.assertEqual([item["status"] for item in queued], ["complete", "error"])
        self.assertEqual(len({item["id"] for item in queued}), 2)
        self.assertEqual(alerts.take_pending(self.db, EMAIL), [])
        self.assertTrue(alerts.is_subscribed(self.db, EMAIL))

    def test_opting_out_keeps_nothing_waiting_and_opting_in_again_starts_empty(self):
        alerts.set_subscription(self.db, EMAIL, True)
        alerts.record_run(self.db, EMAIL, self.card, run=run_with(self.card, [100]))
        self.assertFalse(alerts.set_subscription(self.db, EMAIL, False))
        self.assertEqual(alerts.take_pending(self.db, EMAIL), [])
        self.assertFalse(alerts.record_run(self.db, EMAIL, self.card, run=run_with(self.card, [100])))
        alerts.set_subscription(self.db, EMAIL, True)
        self.assertEqual(alerts.take_pending(self.db, EMAIL), [])

    def test_a_browser_that_stays_closed_keeps_only_the_newest_notifications(self):
        alerts.set_subscription(self.db, EMAIL, True)
        for _ in range(alerts.MAX_PENDING + 5):
            alerts.record_run(self.db, EMAIL, self.card, run=run_with(self.card, [100]))
        self.assertEqual(len(alerts.take_pending(self.db, EMAIL)), alerts.MAX_PENDING)

    def test_subscriptions_are_per_account(self):
        alerts.set_subscription(self.db, EMAIL, True)
        self.assertFalse(alerts.is_subscribed(self.db, "other@example.com"))
        self.assertFalse(alerts.record_run(self.db, "other@example.com", self.card,
                                           run=run_with(self.card, [100])))


class MessageTests(ScheduleTestCase):
    def card_with(self, posts=1, runs=()):
        card = store.add_card(self.db, EMAIL, "Launch", post_url())[0]
        for index in range(2, posts + 1):
            card = store.add_post(self.db, EMAIL, card["id"], post_url(index))[0]
        for index, totals in enumerate(runs, 1):
            store.save_run(self.db, EMAIL, card["id"], run_with(card, totals, index=index))
        return store.load_cards(self.db, EMAIL)[0]

    def test_one_post_reads_like_its_table_row(self):
        card = self.card_with(runs=[[100]])
        body = alerts.run_body(card, run_with(card, [125], index=2))
        self.assertEqual(body, "125 likes + comments · +25.0% · alive")

    def test_a_first_check_reports_the_total_without_a_change_or_status(self):
        card = self.card_with()
        self.assertEqual(alerts.run_body(card, run_with(card, [1234])), "1,234 likes + comments")

    def test_several_posts_are_totalled_and_their_statuses_summarized(self):
        card = self.card_with(posts=3, runs=[[100, 100, 100], [200, 150, 100]])
        body = alerts.run_body(card, run_with(card, [900, 160, 90], index=3))
        self.assertEqual(body, "1,150 likes + comments · +155.6% · 1 Viral · 1 dying · 1 dead")

    def test_missing_counts_are_reported_and_never_treated_as_zero(self):
        card = self.card_with(posts=2, runs=[[100, 100]])
        body = alerts.run_body(card, run_with(card, [150, None], index=2))
        self.assertIn("150 likes + comments", body)
        self.assertIn("+50.0%", body)  # The unreadable post is left out of both sides.
        self.assertIn("1 of 2 posts unavailable", body)
        self.assertEqual(alerts.run_body(card, run_with(card, [None, None], index=3)),
                         "no counts read for 2 posts")

    def test_earlier_view_checks_are_never_compared_with_engagement(self):
        card = self.card_with()
        views = {"id": "e" * 64, "at": "2026-11-01T00:00:00+00:00",
                 "results": [{"post_id": store.card_posts(card)[0]["id"], "views": 9000, "outcome": "ok"}]}
        store.save_run(self.db, EMAIL, card["id"], views)
        card = store.load_cards(self.db, EMAIL)[0]
        self.assertEqual(alerts.run_body(card, run_with(card, [120], index=2)), "120 likes + comments")

    def test_a_partial_run_says_so_and_a_failure_carries_its_reason(self):
        card = self.card_with(runs=[[100]])
        self.assertIn("saved with gaps",
                      alerts.run_body(card, run_with(card, [110], index=2, warning="Browser stopped.")))
        failure = alerts.notification(card, error="Facebook is not connected. Open Scrape Data.")
        self.assertEqual(failure["title"], "Launch · check failed")
        self.assertEqual(failure["body"], "Facebook is not connected. Open Scrape Data.")
        self.assertEqual(failure["status"], "error")
        self.assertEqual(alerts.notification(card, error="")["body"],
                         "The automatic daily check could not run.")

    def test_titles_and_bodies_are_bounded_and_unnamed_cards_keep_their_default(self):
        card = self.card_with()
        self.assertEqual(alerts.card_title({"name": ""}), alerts.DEFAULT_CARD_NAME)
        self.assertEqual(alerts.card_title({"name": "x" * 400}), "x" * alerts.MAX_TITLE_CHARS)
        long = alerts.notification(card, error="y" * 900)
        self.assertEqual(len(long["body"]), alerts.MAX_BODY_CHARS)
        self.assertEqual(alerts.run_body({"posts": []}, run_with(card, [100])), "This card has no posts.")


class ScheduledRunNotificationTests(daily.RunDueFixture):
    """The daily worker announces exactly what it saved, for subscribers only."""

    def test_a_subscriber_is_told_about_each_daily_check(self):
        alerts.set_subscription(self.db, EMAIL, True)
        with self.scraper(250):
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))["ran"], 1)
        queued = alerts.take_pending(self.db, EMAIL)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["title"], "Launch")
        self.assertEqual(queued[0]["body"], "250 likes + comments")
        self.assertEqual(queued[0]["card_id"], self.card["id"])
        with self.scraper(500):
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 0, 9))["ran"], 1)
        self.assertEqual(alerts.take_pending(self.db, EMAIL)[0]["body"],
                         "500 likes + comments · +100.0% · alive")

    def test_an_unsubscribed_account_is_never_queued_anything(self):
        with self.scraper():
            schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))
        self.assertEqual(alerts.take_pending(self.db, EMAIL), [])

    def test_a_blocked_check_is_announced_once_a_day_not_once_a_tick(self):
        alerts.set_subscription(self.db, EMAIL, True)
        disconnected = patch("modules.facebook_remote.remote_facebook_session",
                             return_value=SimpleNamespace(status="disconnected", user_id="", name=""))
        with disconnected, self.scraper():
            schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))
            schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2, 5))
        queued = alerts.take_pending(self.db, EMAIL)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["title"], "Launch · check failed")
        self.assertIn("connect Facebook", queued[0]["body"])

    def test_a_failed_check_is_announced_with_the_reason_it_failed(self):
        from modules.facebook_session import FacebookLoginError

        alerts.set_subscription(self.db, EMAIL, True)
        with patch("modules.velocity_jobs.collect_engagement",
                   new=AsyncMock(side_effect=FacebookLoginError("Reconnect Facebook"))):
            schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))
        queued = alerts.take_pending(self.db, EMAIL)
        self.assertEqual(len(queued), 1)
        self.assertIn("Reconnect Facebook", queued[0]["body"])

    def test_a_queueing_failure_never_costs_the_saved_check(self):
        alerts.set_subscription(self.db, EMAIL, True)
        with patch("modules.velocity_notifications.record_run", side_effect=RuntimeError("offline")), \
                self.scraper():
            self.assertEqual(schedule.run_due(self.db, EMAIL, hosted=True, moment=at(2))["ran"], 1)
        self.assertEqual(store.load_cards(self.db, EMAIL)[0]["run_count"], 1)


class NotificationUITests(daily.DailyStatusFixture):
    def test_the_switch_persists_and_queued_checks_reach_the_browser(self):
        card = store.add_card(self.db, EMAIL, "Launch", post_url())[0]
        app = self.app()
        toggle_key = f"{self.key}_notify_toggle"
        self.assertFalse(app.toggle(key=toggle_key).value)
        self.assertNotIn("velocity_notify_enable", [button.key for button in app.button])
        app.toggle(key=toggle_key).set_value(True).run()
        self.assertFalse(list(app.exception))
        self.assertTrue(alerts.is_subscribed(self.db, EMAIL))
        self.assertIn("velocity_notify_enable", [button.key for button in app.button])

        alerts.record_run(self.db, EMAIL, card, run=run_with(card, [4321]))
        app.button(key="velocity_notify_enable").click().run()
        self.assertFalse(list(app.exception))
        markers = [element.proto.body for element in app.get("html") if "data-sm-notify-id" in element.proto.body]
        self.assertEqual(len(markers), 1)
        self.assertIn('data-sm-notify-title="Launch"', markers[0])
        self.assertIn("4,321 likes + comments", markers[0])
        self.assertEqual(alerts.take_pending(self.db, EMAIL), [])
        # The marker is kept for this session, so granting permission later still shows it.
        app.button(key=f"{self.key}_refresh").click().run()
        self.assertTrue(any("data-sm-notify-id" in element.proto.body for element in app.get("html")))

        app.toggle(key=toggle_key).set_value(False).run()
        self.assertFalse(list(app.exception))
        self.assertFalse(alerts.is_subscribed(self.db, EMAIL))
        self.assertFalse(any("data-sm-notify-id" in element.proto.body for element in app.get("html")))

    def test_an_unreadable_subscription_disables_the_switch_without_breaking_the_page(self):
        store.add_card(self.db, EMAIL, "Launch", post_url())
        with patch("modules.velocity_notifications.is_subscribed", side_effect=RuntimeError("offline")):
            app = self.app()
        self.assertTrue(app.toggle(key=f"{self.key}_notify_toggle").disabled)
        self.assertFalse(list(app.exception))


if __name__ == "__main__":
    unittest.main()
