"""Velocity columns append without dropping, renaming, or rewriting old runs."""

from copy import deepcopy
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from playwright.async_api import async_playwright

from modules import velocity_tracking as store
from modules.velocity_table import velocity_card_sheet
from tests import test_velocity_tracking as fixtures, test_velocity_runs as runs

EMAIL = "owner@example.com"


def headers(sheet):
    return re.findall(r'<th scope=col data-run-id=.*?</th>', sheet)


def first_row_cells(sheet):
    row = sheet.split('<tbody><tr>', 1)[1].split('</tr>', 1)[0]
    return re.findall(r'<td.*?</td>', row, re.S)


class StableHistoryTests(fixtures.VelocityTestCase):
    def setUp(self):
        super().setUp()
        auth = patch("modules.auth.allowed_emails", return_value={EMAIL, "teammate@example.com"})
        auth.start()
        self.addCleanup(auth.stop)
        self.card = store.add_card(self.db, EMAIL, url=fixtures.post_url())[0]

    def save(self, number, **changes):
        run = {**runs.run_for(self.card, number, number * 100), **changes}
        store.save_run(self.db, EMAIL, self.card["id"], run)
        return run

    def displayed_card(self):
        card = store.load_cards(self.db, EMAIL)[0]
        return {**card, "runs": store.load_run_history(self.db, EMAIL, card)}

    def test_every_run_appends_one_column_and_keeps_all_previous_dates_and_cells(self):
        previous_headers, previous_cells = [], []
        for number in range(1, 13):
            # Two distinct collections at the same timestamp must stay distinct.
            self.save(number, at="2026-11-07T01:00:00+00:00")
            card = self.displayed_card()
            sheet = velocity_card_sheet(card)
            current_headers = headers(sheet)
            current_cells = first_row_cells(sheet)[1:1 + number]
            self.assertEqual(len(current_headers), number)
            self.assertEqual(current_headers[:-1], previous_headers)
            self.assertEqual(current_cells[:-1], previous_cells)
            self.assertEqual([r["number"] for r in card["runs"]], list(range(1, number + 1)))
            previous_headers, previous_cells = current_headers, current_cells
        self.assertEqual(len(store.load_cards(self.db, EMAIL)[0]["runs"]), store.CACHED_RUNS)

    def test_retry_cannot_rewrite_a_saved_timestamp_or_count(self):
        original = self.save(1)
        before = self.displayed_card()["runs"]
        changed = deepcopy(original)
        changed["at"] = "2030-01-01T00:00:00+00:00"
        changed["results"][0].update(reactions=9000, engagement=9000)
        store.save_run(self.db, EMAIL, self.card["id"], changed)
        self.assertEqual(self.displayed_card()["runs"], before)

    def test_legacy_archives_and_new_compact_columns_mix_without_comment_payloads(self):
        for number in range(1, 11):
            self.save(number)
        for path, row in self.db.rows.items():
            if '/runs/' in path and row["number"] <= 3:
                row.pop("velocity_results")
                row["results"][0]["comment_sample"] = [{"text": "Old archived comment"}]
        history = self.displayed_card()["runs"]
        self.assertEqual([row["number"] for row in history], list(range(1, 11)))
        self.assertNotIn("comment_sample", str(history))
        self.assertEqual(history[0]["results"][0]["engagement"], 100)

    def test_adding_a_post_does_not_backfill_or_change_existing_columns(self):
        self.save(1)
        before = velocity_card_sheet(self.displayed_card())
        self.card = store.add_post(self.db, EMAIL, self.card["id"], fixtures.post_url(2))[0]
        self.save(2)
        after = velocity_card_sheet(self.displayed_card())
        self.assertEqual(headers(after)[0], headers(before)[0])
        self.assertEqual(first_row_cells(after)[1], first_row_cells(before)[1])
        self.assertIn('Post was not included in this run.', after)

    def test_shared_history_is_per_owner_and_revoked_access_is_rejected(self):
        for number in range(1, 11):
            self.save(number)
        shared = store.load_workspace_cards(self.db, "teammate@example.com")[0]
        self.assertEqual(len(store.load_run_history(self.db, "teammate@example.com", shared)), 10)
        other = store.add_card(self.db, "teammate@example.com", url=fixtures.post_url())[0]
        self.assertEqual(store.load_run_history(self.db, "teammate@example.com", other), [])
        with patch("modules.auth.allowed_emails", return_value={"teammate@example.com"}):
            with self.assertRaises(PermissionError):
                store.load_run_history(self.db, "teammate@example.com", shared)
        with self.assertRaises(PermissionError):
            store.load_run_history(self.db, "outsider@example.com", self.card)

    def test_missing_archive_is_reported_instead_of_silently_rolling_columns(self):
        for number in range(1, 11):
            self.save(number)
        missing = next(path for path, row in self.db.rows.items() if '/runs/' in path and row["number"] == 1)
        del self.db.rows[missing]
        with self.assertRaisesRegex(ValueError, "missing from the archive"):
            self.displayed_card()


class HistoryUITests(fixtures.VelocityTestCase):
    script = fixtures.VelocityUITests.script
    key = fixtures.VelocityUITests.key
    app = fixtures.VelocityUITests.app

    def setUp(self):
        super().setUp()
        for target, value in (("modules.velocity_tracking_ui.database.init_db", self.db),
                              ("modules.auth.verify_access", EMAIL), ("modules.auth.allowed_emails", {EMAIL})):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.card = store.add_card(self.db, EMAIL, url=fixtures.post_url())[0]
        for number in range(1, 11):
            store.save_run(self.db, EMAIL, self.card["id"], runs.run_for(self.card, number))

    def sheet(self, app):
        return next(el.proto.body for el in app.get("html") if 'aria-label="Velocity Tracker"' in el.proto.body)

    def test_open_refresh_and_new_session_keep_complete_columns(self):
        with patch.object(store, "load_run_history", wraps=store.load_run_history) as loader:
            app = self.app()
            app.button(key=f"{self.key}_open_{self.card['id']}").click().run()
            first = headers(self.sheet(app))
            self.assertEqual(len(first), 10)
            app.run()
            self.assertEqual(loader.call_count, 1)
            store.save_run(self.db, EMAIL, self.card["id"], runs.run_for(self.card, 11))
            app.button(key=f"{self.key}_refresh").click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(headers(self.sheet(app))[:-1], first)
            self.assertEqual(loader.call_count, 2)
        fresh = self.app()
        fresh.button(key=f"{self.key}_open_{self.card['id']}").click().run()
        self.assertEqual(headers(self.sheet(fresh)), headers(self.sheet(app)))

    def test_failed_refresh_keeps_old_columns_and_retry_loads_full_history(self):
        app = self.app()
        app.button(key=f"{self.key}_open_{self.card['id']}").click().run()
        before = headers(self.sheet(app))
        store.save_run(self.db, EMAIL, self.card["id"], runs.run_for(self.card, 11))
        with patch.object(store, "load_run_history", side_effect=RuntimeError("offline")):
            app.button(key=f"{self.key}_refresh").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(headers(self.sheet(app)), before)
        self.assertTrue(any("full run history" in warning.value for warning in app.warning))
        next(button for button in app.button if button.label == "Retry loading history").click().run()
        self.assertEqual(headers(self.sheet(app))[:-1], before)
        self.assertFalse(any("full run history" in warning.value for warning in app.warning))

    def test_first_load_failure_does_not_show_eight_runs_as_complete_history(self):
        with patch.object(store, "load_run_history", side_effect=RuntimeError("offline")):
            app = self.app()
            app.button(key=f"{self.key}_open_{self.card['id']}").click().run()
        self.assertFalse(any('aria-label="Velocity Tracker"' in el.proto.body for el in app.get("html")))
        self.assertTrue(any("full run history" in warning.value for warning in app.warning))


class StableColumnLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_widths_and_positions_do_not_change_as_history_grows_on_desktop_or_mobile(self):
        root = Path(__file__).resolve().parents[1]
        css = "\n".join((root / "modules" / "theme" / name).read_text()
                        for name in ("client_report.css", "velocity-notepad.css"))
        card = {"id": "c" * 64, "posts": [{"id": "p", "url": fixtures.post_url()}]}
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            self.addAsyncCleanup(browser.close)
            page = await browser.new_page()
            for viewport in (1280, 390):
                await page.set_viewport_size({"width": viewport, "height": 900})
                previous = []
                for count in (1, 8, 9, 12):
                    card["runs"] = [runs.run_for(card, number, number * 100) for number in range(1, count + 1)]
                    await page.set_content('<style>' + css + '</style><div class="st-key-sm_velocity_modal">'
                                           + velocity_card_sheet(card) + '</div>')
                    bounds = await page.locator('th[data-run-id]').evaluate_all(
                        '(els) => els.map(el => ({x:el.getBoundingClientRect().x,width:el.getBoundingClientRect().width}))')
                    self.assertEqual(bounds[:len(previous)], previous)
                    self.assertEqual(len(bounds), count)
                    for column in bounds:
                        self.assertAlmostEqual(column["width"], 110, places=1)
                    previous = bounds
                wrapper = await page.locator('.crt-table-wrap').evaluate(
                    '(el) => ({client:el.clientWidth, scroll:el.scrollWidth, overflow:getComputedStyle(el).overflowX})')
                self.assertEqual(wrapper["overflow"], "auto")
                self.assertGreater(wrapper["scroll"], wrapper["client"])
