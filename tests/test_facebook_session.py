"""Facebook connection state and the dashboard's visible account controls."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.facebook_session import (
    APP_DIR, FacebookLoginError, FacebookSession, _profile_session, capture_facebook_session,
    check_facebook_session, facebook_context_options, facebook_state_revision,
    facebook_storage_path, facebook_user_id, save_facebook_session,
    saved_facebook_session, saved_facebook_user_id,
)


def cookies(expires=-1, domain=".facebook.com"):
    return [
        {"name": "c_user", "value": "123456", "domain": domain, "expires": expires},
        {"name": "xs", "value": "test-session", "domain": domain, "expires": expires},
    ]


class SavedSessionTests(unittest.TestCase):
    def test_default_session_path_is_independent_of_the_working_directory(self):
        with patch("modules.facebook_session.get_setting", return_value=""), tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                self.assertEqual(facebook_storage_path(), str(APP_DIR / "storage_state.json"))
            finally:
                os.chdir(previous)

    def test_existing_and_session_cookies_are_recognized(self):
        self.assertEqual(facebook_user_id(cookies()), "123456")
        self.assertEqual(facebook_user_id(cookies(time.time() + 3600)), "123456")

    def test_expired_incomplete_or_other_domain_cookies_do_not_connect(self):
        for value in (cookies(time.time() - 1), cookies(domain="notfacebook.com"), cookies()[:1], []):
            with self.subTest(cookies=value):
                self.assertEqual(facebook_user_id(value), "")

    def test_saved_session_file_can_be_missing_malformed_or_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            self.assertEqual(saved_facebook_user_id(str(path)), "")
            for payload in ("invalid", '[]', '{"cookies": [null]}', '{"cookies": {}}'):
                path.write_text(payload)
                self.assertEqual(saved_facebook_user_id(str(path)), "")
            path.write_text(json.dumps({"cookies": cookies()}))
            self.assertEqual(saved_facebook_user_id(str(path)), "123456")


class SessionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_state_restores_identity_and_indexeddb_after_a_new_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.json")
            state = {"cookies": cookies(), "origins": [{
                "origin": "https://www.facebook.com", "localStorage": [{"name": "device", "value": "remembered"}],
                "indexedDB": [{"name": "login", "version": 1, "stores": []}],
            }]}
            context = MagicMock(storage_state=AsyncMock(return_value=state))
            session = FacebookSession("connected", "123456", "Alex Tan")
            self.assertTrue(await save_facebook_session(context, path, None, session=session, user_agent="same-browser"))
            context.storage_state.assert_awaited_once_with(indexed_db=True)
            self.assertEqual(saved_facebook_session(path), session)
            restored = facebook_context_options(path)
            self.assertEqual(restored["storage_state"]["cookies"], cookies())
            self.assertEqual(restored["storage_state"]["origins"], state["origins"])
            self.assertEqual(restored["user_agent"], "same-browser")
            self.assertNotIn("sensemaker", restored["storage_state"])
            if os.name != "nt":
                self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob(".storage_state-*")), [])

    async def test_renewed_cookies_are_saved_and_stale_parallel_writes_cannot_replace_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"cookies": cookies(), "origins": []}))
            original = facebook_state_revision(str(path))
            renewed = cookies(time.time() + 3600)
            renewed[1]["value"] = "renewed-session"
            context = MagicMock(storage_state=AsyncMock(return_value={"cookies": renewed, "origins": []}))
            self.assertTrue(await save_facebook_session(context, str(path), original))
            stale = MagicMock(storage_state=AsyncMock(return_value={"cookies": cookies(), "origins": []}))
            self.assertFalse(await save_facebook_session(stale, str(path), original))
            self.assertEqual(json.loads(path.read_text())["cookies"], renewed)

    async def test_logged_out_context_never_erases_the_existing_login(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            previous = json.dumps({"cookies": cookies(), "origins": []})
            path.write_text(previous)
            context = MagicMock(storage_state=AsyncMock(return_value={"cookies": [], "origins": []}))
            self.assertFalse(await save_facebook_session(context, str(path), facebook_state_revision(str(path))))
            self.assertEqual(path.read_text(), previous)


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.json"
        self.state = {"cookies": cookies(time.time() + 3600), "origins": []}
        self.path.write_text(json.dumps(self.state))
        self.context = MagicMock()
        self.context.storage_state = AsyncMock(return_value=self.state.copy())
        self.context.cookies = AsyncMock(return_value=self.state["cookies"])
        self.page = MagicMock()
        self.page.url = "https://www.facebook.com/"
        self.page.goto = AsyncMock()
        self.page.bring_to_front = AsyncMock()
        self.page.is_closed.return_value = False
        self.page.evaluate = AsyncMock(return_value="stable-chrome")
        self.page.locator.return_value.count = AsyncMock(return_value=0)
        self.context.new_page = AsyncMock(return_value=self.page)
        self.browser = MagicMock(new_context=AsyncMock(return_value=self.context), close=AsyncMock())
        self.browser.is_connected.return_value = True
        runtime = MagicMock()
        runtime.__aenter__ = AsyncMock()
        runtime.__aexit__ = AsyncMock(return_value=False)
        self.addCleanup(patch.stopall)
        patch("playwright.async_api.async_playwright", return_value=runtime).start()
        patch("modules.facebook_session.launch_facebook_browser", AsyncMock(return_value=self.browser)).start()

    def test_connection_check_refreshes_the_file_and_preserves_a_recent_confirmation(self):
        session = FacebookSession("connected", "123456", "Alex Tan")
        with patch("modules.facebook_session._profile_session", AsyncMock(return_value=session)):
            self.assertEqual(check_facebook_session(str(self.path)), session)
        self.assertEqual(saved_facebook_session(str(self.path)), session)
        self.assertEqual(facebook_context_options(str(self.path))["user_agent"], "stable-chrome")
        self.browser.new_context.assert_awaited_once_with(storage_state=self.state)

    def test_network_outage_preserves_saved_login_without_claiming_a_live_connection(self):
        before = self.path.read_bytes()
        with patch("modules.facebook_session._profile_session", AsyncMock(side_effect=TimeoutError)):
            result = check_facebook_session(str(self.path))
        self.assertEqual(result.status, "saved")
        self.assertEqual(result.user_id, "123456")
        self.assertEqual(self.path.read_bytes(), before)

    def test_explicit_login_rejection_requires_reauthentication(self):
        with patch("modules.facebook_session._profile_session", AsyncMock(return_value=FacebookSession())):
            self.assertEqual(check_facebook_session(str(self.path)).status, "disconnected")
        self.assertEqual(saved_facebook_session(str(self.path)).status, "disconnected")

    def test_reopening_login_restores_the_saved_session_instead_of_starting_empty(self):
        session = FacebookSession("connected", "123456", "Alex Tan")
        with patch("modules.facebook_session._profile_session", AsyncMock(return_value=session)) as inspect:
            self.assertEqual(capture_facebook_session(str(self.path)), session)
        inspect.assert_awaited_once_with(self.page, self.context, navigate=False)
        self.page.goto.assert_awaited_once_with("https://www.facebook.com/", wait_until="domcontentloaded")
        self.browser.new_context.assert_awaited_once_with(storage_state=self.state)
        self.assertEqual(saved_facebook_session(str(self.path)), session)

    def test_login_redirect_does_not_close_the_window_before_capture(self):
        from playwright.async_api import Error
        session = FacebookSession("connected", "123456", "Alex Tan")
        transition = Error("Execution context was destroyed, most likely because of a navigation")
        with patch("modules.facebook_session._profile_session", AsyncMock(side_effect=[transition, session])), \
                patch("modules.facebook_session.asyncio.sleep", AsyncMock()):
            self.assertEqual(capture_facebook_session(str(self.path)), session)
        self.assertEqual(saved_facebook_session(str(self.path)), session)
        self.browser.close.assert_awaited_once()

    def test_slow_initial_load_still_allows_login_to_finish(self):
        from playwright.async_api import TimeoutError
        self.page.goto.side_effect = TimeoutError("initial load timed out")
        session = FacebookSession("connected", "123456", "Alex Tan")
        with patch("modules.facebook_session._profile_session", AsyncMock(return_value=session)):
            self.assertEqual(capture_facebook_session(str(self.path)), session)
        self.assertEqual(saved_facebook_session(str(self.path)), session)

    def test_closing_the_window_explains_why_login_was_not_saved(self):
        before = self.path.read_bytes()
        self.page.is_closed.return_value = True
        with self.assertRaisesRegex(FacebookLoginError, "window closed"):
            capture_facebook_session(str(self.path))
        self.assertEqual(self.path.read_bytes(), before)

    def test_timeout_explains_the_required_verification_and_preserves_saved_state(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(FacebookLoginError, "finish signing in and any verification"):
            capture_facebook_session(str(self.path), timeout_seconds=0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_recent_confirmation_survives_dashboard_reload_without_a_browser_check(self):
        state = {**self.state, "sensemaker": {"user_id": "123456", "name": "Alex Tan", "verified_at": time.time()}}
        self.path.write_text(json.dumps(state))
        from dashboard import _get_facebook_session
        with patch("dashboard._check_facebook_session") as check:
            self.assertEqual(_get_facebook_session(str(self.path)).status, "connected")
            self.assertEqual(_get_facebook_session(str(self.path)).status, "connected")
            check.assert_not_called()


class ProfileSessionTests(unittest.IsolatedAsyncioTestCase):
    def make_page(self, url="https://www.facebook.com/example.user", name="Alex Tan"):
        page = MagicMock()
        page.goto = AsyncMock()
        page.url = url
        page.locator.return_value.count = AsyncMock(return_value=0)
        page.locator.return_value.first.inner_text = AsyncMock(return_value=name)
        page.title = AsyncMock(return_value="Facebook")
        context = MagicMock()
        context.cookies = AsyncMock(return_value=cookies())
        return page, context

    async def test_live_profile_provides_the_connected_user(self):
        page, context = self.make_page()
        self.assertEqual(await _profile_session(page, context), FacebookSession("connected", "123456", "Alex Tan"))

    async def test_login_and_checkpoint_redirects_are_not_connected(self):
        for url in ("https://www.facebook.com/login/", "https://www.facebook.com/checkpoint/", "https://notfacebook.com/profile"):
            page, context = self.make_page(url=url)
            self.assertEqual((await _profile_session(page, context)).status, "disconnected")

    async def test_cookie_alone_does_not_claim_a_confirmed_connection(self):
        page, context = self.make_page(name="")
        self.assertEqual((await _profile_session(page, context)).status, "unavailable")

    async def test_passive_login_check_waits_for_account_controls_without_navigating(self):
        page, context = self.make_page(url="https://www.facebook.com/")
        self.assertEqual((await _profile_session(page, context, navigate=False)).status, "unavailable")
        page.goto.assert_not_awaited()

    async def test_passive_login_captures_the_account_without_interrupting_the_page(self):
        page, context = self.make_page(url="https://www.facebook.com/")
        password = MagicMock(count=AsyncMock(return_value=0))
        account = MagicMock(count=AsyncMock(return_value=1))
        profile = MagicMock()
        profile.first.count = AsyncMock(return_value=1)
        profile.first.get_attribute = AsyncMock(return_value="Alex Tan's timeline")
        page.locator.side_effect = [password, account, profile]
        self.assertEqual(await _profile_session(page, context, navigate=False),
                         FacebookSession("connected", "123456", "Alex Tan"))
        page.goto.assert_not_awaited()


class ConnectionCardTests(unittest.TestCase):
    script = ("from dashboard import _render_facebook_connection, _get_facebook_session, _facebook_storage_path\n"
              "from modules.navigation import dashboard_views\n"
              "session = _get_facebook_session(_facebook_storage_path())\n"
              "views = dashboard_views('editor@example.com', session)\n"
              "with views[0]:\n"
              "    _render_facebook_connection(session)\n")

    @patch.dict(os.environ, {"PLAYWRIGHT_STORAGE_STATE": "/tmp/sensemaker-test-state.json"})
    def test_signed_out_card_has_one_direct_login_button_without_an_expander(self):
        with patch("dashboard._get_facebook_session", return_value=FacebookSession()), patch("dashboard.IS_CLOUD", False):
            app = AppTest.from_string(self.script).run()
            self.assertFalse(list(app.exception))
            self.assertEqual([button.label for button in app.button], ["Log in to Facebook"])
            self.assertFalse(app.button[0].disabled)
            self.assertEqual(len(app.expander), 0)

    def test_connected_account_moves_above_google_in_toolbar_and_hides_main_card(self):
        with patch("dashboard._get_facebook_session", return_value=FacebookSession("connected", "123456", "Alex <Tan>")), patch.dict(os.environ):
            app = AppTest.from_string(self.script).run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.button), 0)
            self.assertFalse(any("fb-identity" in item.value for item in app.main.markdown))
            content = " ".join(item.value for item in app.sidebar.markdown)
            self.assertIn("Alex &lt;Tan&gt;", content)
            self.assertIn("Connected", content)
            self.assertLess(content.index("sm-rail-facebook"), content.index("Signed in as editor@example.com"))

    def test_saved_session_offers_a_connection_check_instead_of_another_login(self):
        with patch("dashboard._get_facebook_session", return_value=FacebookSession("saved", "123456", "Alex Tan")):
            app = AppTest.from_string(self.script).run()
            self.assertFalse(list(app.exception))
            self.assertEqual([button.label for button in app.button], ["Check Facebook connection"])
            self.assertIn("Facebook · Saved", " ".join(item.value for item in app.sidebar.markdown))

    def test_login_failure_shows_the_actionable_reason(self):
        with patch("dashboard._get_facebook_session", return_value=FacebookSession()), \
                patch("dashboard.IS_CLOUD", False), \
                patch("dashboard.capture_facebook_session", side_effect=FacebookLoginError("The Facebook window closed.")):
            app = AppTest.from_string(self.script).run()
            app.button[0].click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.error[0].value, "The Facebook window closed.")

    @patch.dict(os.environ, {"PLAYWRIGHT_STORAGE_STATE": "/tmp/sensemaker-test-state.json"})
    def test_successful_login_replaces_the_button_on_the_same_interaction(self):
        sessions = [FacebookSession(), FacebookSession(), FacebookSession("connected", "123456", "Alex Tan")]
        with patch("dashboard._get_facebook_session", side_effect=sessions), patch("dashboard.IS_CLOUD", False), patch("dashboard.capture_facebook_session") as capture:
            app = AppTest.from_string(self.script).run()
            app.button[0].click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.button), 0)
            self.assertIn("Alex Tan", " ".join(item.value for item in app.sidebar.markdown))
            self.assertFalse(any("fb-identity" in item.value for item in app.main.markdown))
            capture.assert_called_once_with(str(Path("/tmp/sensemaker-test-state.json").resolve()), timeout_seconds=240)


if __name__ == "__main__":
    unittest.main()
