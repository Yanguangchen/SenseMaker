"""Hosted login, account isolation and remote-browser lifecycle regressions."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.facebook_session import FacebookLoginError, FacebookSession
from modules import facebook_remote as remote


class HostedLoginUITests(unittest.TestCase):
    script = (
        "from dashboard import _render_facebook_connection\n"
        "from modules.facebook_session import FacebookSession\n"
        "_render_facebook_connection(FacebookSession(), user_email='editor@example.com')\n"
    )

    def test_hosted_login_is_enabled_and_starts_remote_instead_of_desktop_browser(self):
        with patch("dashboard.IS_CLOUD", True), patch("dashboard.remote_facebook_configured", return_value=True), \
             patch("dashboard.start_login", return_value="private-job") as start, \
             patch("dashboard._render_remote_facebook_progress") as progress, \
             patch("dashboard.capture_facebook_session") as desktop, \
             patch("modules.auth.verify_access", return_value="editor@example.com"):
            app = AppTest.from_string(self.script).run()
            self.assertFalse(app.button(key="capture_fb_session_btn").disabled)
            app.button(key="capture_fb_session_btn").click().run()
            self.assertFalse(list(app.exception))
            start.assert_called_once_with("editor@example.com", check_only=False)
            progress.assert_called_with("editor@example.com", "private-job")
            desktop.assert_not_called()

    def test_revoked_google_login_cannot_start_a_remote_browser_from_a_callback(self):
        with patch("dashboard.IS_CLOUD", True), patch("dashboard.remote_facebook_configured", return_value=True), \
             patch("dashboard.start_login") as start, \
             patch("modules.auth.verify_access", side_effect=PermissionError("Revoked")):
            app = AppTest.from_string(self.script).run()
            app.button(key="capture_fb_session_btn").click().run()
            start.assert_not_called()

    def test_save_requests_verification_and_incomplete_login_keeps_viewer_open(self):
        job = remote.LoginJob(status="waiting", live_url="https://www.browserbase.com/devtools?session=test",
                              notice="Finish any verification in the window.")
        with patch("dashboard.IS_CLOUD", True), patch("dashboard.login_job", return_value=job), \
             patch("modules.auth.verify_access", return_value="editor@example.com"):
            app = AppTest.from_string(self.script)
            app.session_state["facebook_login_job"] = "private-job"
            app.run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.get("iframe")), 1)
            self.assertIn("verification", app.warning[0].value)
            app.button(key="save_remote_facebook_login").click().run()
            self.assertTrue(job.finish.is_set())
            self.assertEqual(len(app.get("iframe")), 1)
            self.assertTrue(any("Checking your Facebook" in item.value for item in app.info))

    def test_completed_login_removes_viewer_and_confirms_saved(self):
        job = remote.LoginJob(status="complete")
        with patch("dashboard.IS_CLOUD", True), patch("dashboard.remote_facebook_configured", return_value=True), \
             patch("dashboard.login_job", return_value=job), patch("dashboard.forget_login") as forget, \
             patch("modules.auth.verify_access", return_value="editor@example.com"):
            app = AppTest.from_string(self.script)
            app.session_state["facebook_login_job"] = "private-job"
            app.run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.get("iframe")), 0)
            self.assertIn("login saved", app.success[0].value)
            forget.assert_called_once_with("editor@example.com", "private-job")

    def test_saved_login_offers_both_retry_and_interactive_reconnection(self):
        script = self.script.replace("FacebookSession()", "FacebookSession('saved', '123')")
        with patch("dashboard.IS_CLOUD", True), patch("dashboard.remote_facebook_configured", return_value=True):
            app = AppTest.from_string(script).run()
            self.assertFalse(list(app.exception))
            self.assertFalse(app.button(key="check_fb_session_btn").disabled)
            self.assertFalse(app.button(key="capture_fb_session_btn").disabled)


class ProfileTests(unittest.TestCase):
    def test_owner_profiles_and_job_handles_are_separate(self):
        db = MagicMock()
        with patch("modules.facebook_remote.init_db", return_value=db):
            remote._profile_ref("first@example.com")
            first = db.collection.return_value.document.call_args.args[0]
            remote._profile_ref("second@example.com")
            second = db.collection.return_value.document.call_args.args[0]
        self.assertNotEqual(first, second)
        with patch.dict(remote._jobs, {}, clear=True), patch("modules.facebook_remote.threading.Thread"):
            first_job = remote.start_login("first@example.com")
            self.assertEqual(remote.start_login("FIRST@example.com"), first_job)
            self.assertIsNone(remote.login_job("second@example.com", first_job))
            self.assertNotEqual(remote.start_login("second@example.com"), first_job)

    def test_a_profile_cannot_be_used_concurrently_in_another_container(self):
        ref = MagicMock()
        ref.get.return_value.to_dict.return_value = {"busy_until": time.time() + 120}
        with patch("modules.facebook_remote._ensure_profile", return_value="profile"), \
             patch("modules.facebook_remote._profile_ref", return_value=ref), \
             patch("modules.facebook_remote.init_db") as db, \
             patch("modules.facebook_remote.firestore.transactional", side_effect=lambda function: function):
            with self.assertRaisesRegex(FacebookLoginError, "already open"):
                remote._acquire("editor@example.com", 240)
            db.return_value.transaction.return_value.update.assert_not_called()

    def test_stale_worker_cannot_release_a_newer_lease_or_replace_its_identity(self):
        ref = MagicMock()
        ref.get.return_value.to_dict.return_value = {"lease": "new-worker"}
        with patch("modules.facebook_remote._profile_ref", return_value=ref), \
             patch("modules.facebook_remote.init_db") as db, \
             patch("modules.facebook_remote.firestore.transactional", side_effect=lambda function: function):
            remote._release("editor@example.com", "old-worker", FacebookSession("connected", "123", "Alex"))
            db.return_value.transaction.return_value.update.assert_not_called()

    def test_login_metadata_survives_process_restart_and_ages_into_saved(self):
        record = {"context_id": "private-profile", "user_id": "123", "name": "Alex", "verified_at": time.time()}
        with patch("modules.facebook_remote.remote_facebook_configured", return_value=True), \
             patch("modules.facebook_remote._record", return_value=record):
            self.assertEqual(remote.remote_facebook_session("editor@example.com").status, "connected")
            record["verification_pending"] = True
            self.assertEqual(remote.remote_facebook_session("editor@example.com").status, "saved")
            record["verification_pending"] = False
            record["verified_at"] -= 600
            self.assertEqual(remote.remote_facebook_session("editor@example.com").status, "saved")
            record["needs_login"] = True
            self.assertEqual(remote.remote_facebook_session("editor@example.com").status, "disconnected")

    def test_unavailable_check_preserves_identity_and_requests_a_retry_without_marking_logout(self):
        ref = MagicMock()
        ref.get.return_value.to_dict.return_value = {"lease": "worker", "user_id": "123", "verified_at": time.time()}
        with patch("modules.facebook_remote._profile_ref", return_value=ref), \
             patch("modules.facebook_remote.init_db") as db, \
             patch("modules.facebook_remote.firestore.transactional", side_effect=lambda function: function):
            remote._release("editor@example.com", "worker", FacebookSession(status="unavailable"))
            updates = db.return_value.transaction.return_value.update.call_args.args[1]
        self.assertTrue(updates["verification_pending"])
        self.assertNotIn("needs_login", updates)
        self.assertNotIn("user_id", updates)
        self.assertNotIn("verified_at", updates)


class BrowserLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("modules.facebook_remote.remote_facebook_configured", return_value=True).start()
        patch("modules.facebook_remote.get_setting", return_value="server-secret").start()
        patch("modules.facebook_remote._acquire", return_value=("private-profile", "lease")).start()
        self.release = patch("modules.facebook_remote._release").start()
        self.sleep = patch("modules.facebook_remote.asyncio.sleep", new_callable=AsyncMock).start()
        self.bb = MagicMock()
        self.bb.sessions.create = AsyncMock(return_value=SimpleNamespace(id="remote-session", connect_url="wss://private-key"))
        self.bb.sessions.debug = AsyncMock(return_value=SimpleNamespace(
            debugger_fullscreen_url="https://www.browserbase.com/devtools?session=private"))
        self.bb.sessions.update = AsyncMock()
        sdk = MagicMock(__aenter__=AsyncMock(return_value=self.bb), __aexit__=AsyncMock(return_value=False))
        patch("modules.facebook_remote.AsyncBrowserbase", return_value=sdk).start()
        self.context = MagicMock(new_page=AsyncMock())
        self.browser = MagicMock(contexts=[self.context], close=AsyncMock())
        self.playwright = MagicMock()
        self.playwright.chromium.connect_over_cdp = AsyncMock(return_value=self.browser)

    def login_page(self):
        page = MagicMock(goto=AsyncMock(), is_closed=MagicMock(return_value=False))
        self.context.pages = [page]
        self.context.browser = self.browser
        self.browser.is_connected.return_value = True
        sdk = MagicMock(__aenter__=AsyncMock(return_value=self.playwright), __aexit__=AsyncMock(return_value=False))
        patch("playwright.async_api.async_playwright", return_value=sdk).start()
        return page

    async def test_saved_profile_records_no_login_video_or_logs_and_always_releases(self):
        urls = []
        identity = FacebookSession("connected", "123", "Alex")
        with patch("modules.facebook_remote._profile_session", AsyncMock(return_value=identity)):
            async with remote.remote_facebook_context(self.playwright, "editor@example.com", on_ready=urls.append) as context:
                self.assertIs(context, self.context)
        settings = self.bb.sessions.create.call_args.kwargs["browser_settings"]
        self.assertEqual(settings["context"], {"id": "private-profile", "persist": True})
        self.assertFalse(settings["record_session"])
        self.assertFalse(settings["log_session"])
        self.assertFalse(settings["solve_captchas"])
        self.assertTrue(urls[0].startswith("https://www.browserbase.com/"))
        self.assertNotIn("private-key", urls[0])
        self.browser.close.assert_awaited_once()
        self.bb.sessions.update.assert_awaited_once()
        self.release.assert_called_once_with("editor@example.com", "lease", identity)

    async def test_failed_cdp_connection_still_terminates_the_remote_session_without_leaking_its_url(self):
        self.playwright.chromium.connect_over_cdp.side_effect = RuntimeError("wss://private-key")
        with self.assertRaises(FacebookLoginError) as caught:
            async with remote.remote_facebook_context(self.playwright, "editor@example.com"):
                self.fail("Failed connection must not yield a browser")
        self.assertNotIn("private-key", str(caught.exception))
        self.bb.sessions.update.assert_awaited_once()
        self.release.assert_called_once_with("editor@example.com", "lease", None)

    async def test_expired_facebook_login_blocks_collection_and_marks_reauthentication(self):
        with patch("modules.facebook_remote._profile_session", AsyncMock(return_value=FacebookSession())):
            with self.assertRaisesRegex(FacebookLoginError, "asking you to sign in"):
                async with remote.remote_facebook_context(self.playwright, "editor@example.com", check_before_use=True):
                    self.fail("Logged-out Facebook pages must not be collected")
        self.release.assert_called_once_with("editor@example.com", "lease", FacebookSession())
        self.browser.close.assert_awaited_once()

    async def test_inconclusive_check_does_not_report_an_expired_login_or_allow_collection(self):
        identity = FacebookSession(status="unavailable")
        with patch("modules.facebook_remote._profile_session", AsyncMock(return_value=identity)):
            with self.assertRaisesRegex(FacebookLoginError, "saved login has been kept") as caught:
                async with remote.remote_facebook_context(self.playwright, "editor@example.com", check_before_use=True):
                    self.fail("An unconfirmed session must not start collection")
        self.assertNotIn("renewed", str(caught.exception))
        self.release.assert_called_once_with("editor@example.com", "lease", identity)

    async def test_signed_in_viewer_stays_open_until_save_and_uses_that_page_for_verification(self):
        page = self.login_page()
        job = remote.LoginJob()
        identity = FacebookSession("connected", "123", "Alex")
        waiting_ticks = 0
        with patch("modules.facebook_remote._profile_session", AsyncMock(return_value=identity)) as inspect:
            async def tick(seconds):
                nonlocal waiting_ticks
                if seconds == 0.5:
                    waiting_ticks += 1
                    self.assertEqual(job.status, "waiting")
                    self.assertTrue(job.live_url)
                    self.browser.close.assert_not_awaited()
                    inspect.assert_not_awaited()
                    if waiting_ticks == 3:
                        job.finish.set()
            self.sleep.side_effect = tick
            await remote._login("editor@example.com", job, False)
            self.assertEqual(inspect.await_count, 2)
            inspect.assert_awaited_with(page, self.context, navigate=False)
        self.assertEqual(waiting_ticks, 3)
        self.assertEqual(job.status, "saving")
        self.assertEqual(job.live_url, "")
        self.context.new_page.assert_not_awaited()
        self.release.assert_called_once_with("editor@example.com", "lease", identity)
        self.browser.close.assert_awaited_once()

    async def test_unfinished_verification_does_not_close_the_viewer_and_save_can_be_retried(self):
        self.login_page()
        job = remote.LoginJob()
        job.finish.set()
        identity = FacebookSession("connected", "123", "Alex")
        async def tick(seconds):
            if seconds == 0.5:
                self.assertEqual(job.status, "waiting")
                self.assertIn("Finish any verification", job.notice)
                self.assertTrue(job.live_url)
                self.browser.close.assert_not_awaited()
                job.finish.set()
        self.sleep.side_effect = tick
        with patch("modules.facebook_remote._profile_session", AsyncMock(side_effect=[FacebookSession(), identity, identity])):
            await remote._login("editor@example.com", job, False)
        self.release.assert_called_once_with("editor@example.com", "lease", identity)

    async def test_switching_accounts_must_settle_before_saving(self):
        self.login_page()
        job = remote.LoginJob()
        job.finish.set()
        first = FacebookSession("connected", "123", "Alex")
        second = FacebookSession("connected", "456", "Sam")
        async def tick(seconds):
            if seconds == 0.5:
                self.browser.close.assert_not_awaited()
                self.assertEqual(job.status, "waiting")
                job.finish.set()
        self.sleep.side_effect = tick
        with patch("modules.facebook_remote._profile_session", AsyncMock(side_effect=[first, second, second, second])):
            await remote._login("editor@example.com", job, False)
        self.release.assert_called_once_with("editor@example.com", "lease", second)

    async def test_cancel_releases_browser_without_saving_an_unverified_login(self):
        self.login_page()
        job = remote.LoginJob()
        job.cancel.set()
        with self.assertRaisesRegex(FacebookLoginError, "cancelled"):
            await remote._login("editor@example.com", job, False)
        self.browser.close.assert_awaited_once()
        self.bb.sessions.update.assert_awaited_once()
        self.release.assert_called_once_with("editor@example.com", "lease", None)

    async def test_disconnected_browser_reports_recovery_and_releases_profile(self):
        self.login_page()
        self.browser.is_connected.return_value = False
        with self.assertRaisesRegex(FacebookLoginError, "disconnected. Open it again"):
            await remote._login("editor@example.com", remote.LoginJob(), False)
        self.release.assert_called_once_with("editor@example.com", "lease", None)

    async def test_login_timeout_releases_browser_and_explains_expiry(self):
        self.login_page()
        with patch("modules.facebook_remote.LOGIN_SECONDS", 0), self.assertRaisesRegex(FacebookLoginError, "expired"):
            await remote._login("editor@example.com", remote.LoginJob(), False)
        self.browser.close.assert_awaited_once()
        self.bb.sessions.update.assert_awaited_once()
        self.release.assert_called_once_with("editor@example.com", "lease", None)

    async def test_saved_connection_is_checked_once_without_opening_an_interactive_viewer(self):
        self.login_page()
        identity = FacebookSession("connected", "123", "Alex")
        with patch("modules.facebook_remote._profile_session", AsyncMock(return_value=identity)) as inspect:
            await remote._login("editor@example.com", remote.LoginJob(), True)
            inspect.assert_awaited_once()
        self.bb.sessions.debug.assert_not_awaited()
        self.release.assert_called_once_with("editor@example.com", "lease", identity)


if __name__ == "__main__":
    unittest.main()
