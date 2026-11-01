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
            record["verified_at"] -= 600
            self.assertEqual(remote.remote_facebook_session("editor@example.com").status, "saved")
            record["needs_login"] = True
            self.assertEqual(remote.remote_facebook_session("editor@example.com").status, "disconnected")


class BrowserLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("modules.facebook_remote.remote_facebook_configured", return_value=True).start()
        patch("modules.facebook_remote.get_setting", return_value="server-secret").start()
        patch("modules.facebook_remote._acquire", return_value=("private-profile", "lease")).start()
        self.release = patch("modules.facebook_remote._release").start()
        patch("modules.facebook_remote.asyncio.sleep", new_callable=AsyncMock).start()
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
            with self.assertRaisesRegex(FacebookLoginError, "renewed"):
                async with remote.remote_facebook_context(self.playwright, "editor@example.com", check_before_use=True):
                    self.fail("Logged-out Facebook pages must not be collected")
        self.release.assert_called_once_with("editor@example.com", "lease", FacebookSession())
        self.browser.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
