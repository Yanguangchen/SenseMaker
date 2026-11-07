import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from modules.browserbase_usage import BrowserbaseUsage, browser_allowance, load_browserbase_usage
from modules.browserbase_usage_ui import _usage_markup


class BrowserbaseUsageTests(unittest.TestCase):
    def setUp(self):
        load_browserbase_usage.clear()
        self.addCleanup(load_browserbase_usage.clear)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.sdk = self.stack.enter_context(patch("modules.browserbase_usage.Browserbase"))
        self.bb = self.sdk.return_value.__enter__.return_value
        self.bb.projects.retrieve.return_value = SimpleNamespace(id="project", concurrency=3)
        self.bb.projects.usage.return_value = SimpleNamespace(browser_minutes=60, proxy_bytes=2500000)

    def test_reads_real_project_fields_and_caches_without_creating_sessions(self):
        usage = load_browserbase_usage("server-key", "project")
        self.assertEqual((usage.browser_minutes, usage.proxy_bytes, usage.concurrency), (60, 2500000, 3))
        self.assertGreater(usage.checked_at, 0)
        self.assertEqual(load_browserbase_usage("server-key", "project"), usage)
        self.bb.projects.retrieve.assert_called_once_with("project")
        self.bb.projects.usage.assert_called_once_with("project")
        self.bb.sessions.create.assert_not_called()
        self.assertNotIn("server-key", repr(usage))
        load_browserbase_usage("new-key", "project")
        self.assertEqual(self.sdk.call_count, 2)

    def test_single_project_is_discovered_but_multiple_projects_are_not_guessed(self):
        self.bb.projects.list.return_value = [SimpleNamespace(id="only", concurrency=25)]
        usage = load_browserbase_usage("key", "")
        self.assertEqual(usage.concurrency, 25)
        self.bb.projects.usage.assert_called_once_with("only")
        self.bb.projects.list.return_value = [SimpleNamespace(id="one"), SimpleNamespace(id="two")]
        usage = load_browserbase_usage("another-key", "")
        self.assertIn("Choose the Browserbase project", usage.error)
        self.assertEqual(self.bb.projects.usage.call_count, 1)

    def test_failure_is_cached_redacted_and_can_be_explicitly_refreshed(self):
        self.bb.projects.usage.side_effect = RuntimeError("secret-key and provider response")
        failed = load_browserbase_usage("secret-key", "project")
        self.assertIn("temporarily unavailable", failed.error)
        self.assertNotIn("secret-key", repr(failed))
        self.assertEqual(load_browserbase_usage("secret-key", "project"), failed)
        self.bb.projects.usage.assert_called_once()
        load_browserbase_usage.clear("secret-key", "project")
        self.bb.projects.usage.side_effect = None
        self.assertFalse(load_browserbase_usage("secret-key", "project").error)

    def test_invalid_metrics_and_missing_credentials_do_not_become_zero_usage(self):
        self.assertTrue(load_browserbase_usage("", "").error)
        self.sdk.assert_not_called()
        for value in (-1, float("nan"), None):
            with self.subTest(value=value):
                self.bb.projects.usage.return_value = SimpleNamespace(browser_minutes=value, proxy_bytes=0)
                self.assertTrue(load_browserbase_usage(str(value), "project").error)

    def test_plan_and_custom_allowances_are_explicit_and_invalid_values_are_unknown(self):
        for settings, expected in [({}, ("Free allowance", 60)),
                                   ({"BROWSERBASE_PLAN": "developer"}, ("Developer allowance", 6000)),
                                   ({"BROWSERBASE_PLAN": "startup"}, ("Startup allowance", 30000)),
                                   ({"BROWSERBASE_PLAN": "custom"}, ("Allowance unavailable", None)),
                                   ({"BROWSERBASE_BROWSER_HOURS_LIMIT": "2.5"}, ("Configured allowance", 150)),
                                   ({"BROWSERBASE_BROWSER_HOURS_LIMIT": "nan"}, ("Allowance unavailable", None)),
                                   ({"BROWSERBASE_BROWSER_HOURS_LIMIT": "0"}, ("Allowance unavailable", None))]:
            with self.subTest(settings=settings), patch("modules.browserbase_usage.get_setting", side_effect=lambda k, d="": settings.get(k, d)):
                self.assertEqual(browser_allowance(), expected)

    def test_bar_clamps_overage_and_never_shows_negative_remaining_or_guessed_zero(self):
        for minutes, tone in [(0, "normal"), (47, "normal"), (48, "near"), (60, "full"), (75, "full")]:
            with self.subTest(minutes=minutes):
                markup = _usage_markup(BrowserbaseUsage(minutes, 0, 3, 100), "Free allowance", 60, stale=False)
                self.assertIn(f"sm-bb--{tone}", markup)
                self.assertNotIn("-15 min remaining", markup)
                if minutes == 75:
                    self.assertIn('aria-valuenow="100.00"', markup)
                    self.assertIn("15 min over allowance", markup)
        unknown = _usage_markup(BrowserbaseUsage(60, 0, 3, 100), "Allowance unavailable", None, stale=False)
        self.assertNotIn('role="progressbar"', unknown)
        self.assertIn("Confirm your allowance", unknown)


class BrowserbaseUsageUITests(unittest.TestCase):
    script = (
        "import streamlit as st\n"
        "from modules.browserbase_usage_ui import render_browserbase_usage_bar\n"
        "render_browserbase_usage_bar('editor@example.com')\n"
        "st.subheader('Scrape Data')\n"
    )

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.settings = {"BROWSERBASE_API_KEY": "server-only-key", "BROWSERBASE_PROJECT_ID": "project"}
        self.stack.enter_context(patch("modules.browserbase_usage_ui.get_setting", side_effect=lambda k, d="": self.settings.get(k, d)))
        self.stack.enter_context(patch("modules.browserbase_usage_ui.browser_allowance", return_value=("Free allowance", 60)))
        self.verify = self.stack.enter_context(patch("modules.auth.verify_access", return_value="editor@example.com"))
        self.stack.enter_context(patch("modules.auth.allowed_emails", return_value={"editor@example.com"}))
        self.load = self.stack.enter_context(patch("modules.browserbase_usage_ui.load_browserbase_usage",
                                                return_value=BrowserbaseUsage(48, 0, 3, 100)))

    def app(self, script=None):
        app = AppTest.from_string(script or self.script)
        app.session_state["auth_token"] = "signed-token"
        return app.run()

    def test_bar_shows_remaining_and_refreshes_only_safe_aggregates(self):
        app = self.app()
        self.assertFalse(app.exception)
        self.assertIn("12 min remaining · 80% used", app.markdown[0].value)
        self.assertIn("3 concurrent browsers max", app.markdown[0].value)
        self.assertNotIn("server-only-key", str(app))
        self.load.return_value = BrowserbaseUsage(60, 0, 3, 120)
        app.button(key="refresh_browserbase_usage").click().run()
        self.assertFalse(app.exception)
        self.load.clear.assert_called_once_with("server-only-key", "project")
        self.assertIn("Allowance used", app.markdown[0].value)
        self.assertIn("0 min remaining · 100% used", app.markdown[0].value)

    def test_outage_keeps_last_success_as_stale_and_recovers(self):
        app = self.app()
        self.load.return_value = BrowserbaseUsage(error="Usage unavailable")
        app.run()
        self.assertIn("12 min remaining", app.markdown[0].value)
        self.assertIn("Refresh unavailable", app.markdown[0].value)
        self.load.return_value = BrowserbaseUsage(52, 0, 3, 160)
        app.run()
        self.assertIn("8 min remaining", app.markdown[0].value)
        self.assertNotIn("Refresh unavailable", app.markdown[0].value)

    def test_credential_change_does_not_reuse_another_projects_stale_reading(self):
        app = self.app()
        self.settings["BROWSERBASE_PROJECT_ID"] = "different-project"
        self.load.return_value = BrowserbaseUsage(error="Usage unavailable")
        app.run()
        self.assertEqual(app.markdown[0].value, "**Browserbase** · Usage unavailable")
        self.assertNotIn("browserbase_usage_snapshot", app.session_state)

    def test_missing_credentials_or_failed_first_reading_do_not_render_a_false_meter(self):
        self.settings["BROWSERBASE_API_KEY"] = ""
        app = self.app()
        self.load.assert_not_called()
        self.assertTrue(app.button(key="refresh_browserbase_usage").disabled)
        self.assertIn("Not configured", app.markdown[0].value)
        self.settings["BROWSERBASE_API_KEY"] = "key"
        self.load.return_value = BrowserbaseUsage(error="Usage unavailable")
        app.run()
        self.assertNotIn('role="progressbar"', app.markdown[0].value)

    def test_fragment_rechecks_access_before_fetching_and_clears_snapshot_on_revocation(self):
        self.verify.side_effect = PermissionError("Revoked")
        script = (
            "import streamlit as st\n"
            "from modules.browserbase_usage_ui import render_browserbase_usage_bar\n"
            "if not st.session_state.get('attempted'):\n"
            "    st.session_state['attempted'] = True\n"
            "    render_browserbase_usage_bar('editor@example.com')\n"
            "else:\n"
            "    st.write('Returned to app access gate')\n"
        )
        app = AppTest.from_string(script)
        app.session_state["auth_token"] = "revoked-token"
        app.session_state["browserbase_usage_snapshot"] = ("old", BrowserbaseUsage(48, 0, 3, 100))
        app.run()
        self.assertFalse(app.exception)
        self.load.assert_not_called()
        self.assertNotIn("browserbase_usage_snapshot", app.session_state)
        self.verify.assert_called_once_with("revoked-token", {"editor@example.com"})


if __name__ == "__main__":
    unittest.main()
