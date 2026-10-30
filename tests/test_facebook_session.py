"""Facebook connection state and the dashboard's visible account controls."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from streamlit.testing.v1 import AppTest

from modules.facebook_session import FacebookSession, _profile_session, facebook_user_id, saved_facebook_user_id


def cookies(expires=-1, domain=".facebook.com"):
    return [
        {"name": "c_user", "value": "123456", "domain": domain, "expires": expires},
        {"name": "xs", "value": "test-session", "domain": domain, "expires": expires},
    ]


class SavedSessionTests(unittest.TestCase):
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
