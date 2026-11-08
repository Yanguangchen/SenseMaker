"""The in-page delivery script, exercised in Chromium with a stubbed Notification."""

from pathlib import Path
import unittest

from playwright.async_api import async_playwright

SCRIPT = (Path(__file__).resolve().parents[1] / "modules" / "theme" / "run-notifications.js").read_text(encoding="utf-8")

# Records what the page asked the browser to show, without a real permission prompt.
STUB = """(permission) => {
  window.__shown = [];
  window.__asked = 0;
  class FakeNotification {
    constructor(title, options) {
      if (FakeNotification.permission !== 'granted') throw new TypeError('not allowed');
      window.__shown.push({title, body: (options || {}).body, tag: (options || {}).tag,
                           icon: (options || {}).icon});
    }
    close() {}
    static requestPermission() {
      window.__asked += 1;
      FakeNotification.permission = 'granted';
      return Promise.resolve('granted');
    }
  }
  FakeNotification.permission = permission;
  window.Notification = FakeNotification;
}"""

PAGE = """<body><div id="app"></div></body>"""


class NotificationScriptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        await self.page.route("**/*", lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=PAGE))
        await self.page.goto("https://crisisradar.test/")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def install(self, permission="granted"):
        await self.page.evaluate(STUB, permission)
        await self.page.evaluate(SCRIPT)

    async def marker(self, ident, title="Launch", body="250 likes + comments"):
        await self.page.evaluate(
            """([id, title, body]) => {
                const span = document.createElement('span');
                span.hidden = true;
                span.dataset.smNotifyId = id;
                span.dataset.smNotifyTitle = title;
                span.dataset.smNotifyBody = body;
                document.getElementById('app').appendChild(span);
            }""", [ident, title, body])
        await self.page.wait_for_timeout(250)  # The script debounces DOM changes.

    async def shown(self):
        return await self.page.evaluate("window.__shown")

    async def test_a_queued_check_is_shown_once_with_its_title_body_and_icon(self):
        await self.install()
        await self.marker("run-1")
        self.assertEqual(await self.shown(), [{"title": "Launch", "body": "250 likes + comments",
                                               "tag": "run-1", "icon": "/app/static/icon-192.png"}])
        await self.marker("run-1")
        await self.page.evaluate("document.getElementById('app').innerHTML = ''")
        await self.marker("run-1")
        self.assertEqual(len(await self.shown()), 1)
        await self.marker("run-2", title="Launch · check failed", body="Facebook is not connected.")
        self.assertEqual([item["title"] for item in await self.shown()],
                         ["Launch", "Launch · check failed"])

    async def test_a_reload_does_not_repeat_a_check_already_shown(self):
        await self.install()
        await self.marker("run-1")
        await self.page.reload()
        await self.install()
        await self.marker("run-1")
        self.assertEqual(await self.shown(), [])
        await self.marker("run-3")
        self.assertEqual([item["tag"] for item in await self.shown()], ["run-3"])

    async def test_nothing_is_requested_or_lost_before_permission_is_granted(self):
        await self.install("default")
        await self.marker("run-1")
        self.assertEqual(await self.shown(), [])
        self.assertEqual(await self.page.evaluate("window.__asked"), 0)
        await self.page.evaluate(
            """() => {
                const wrapper = document.createElement('div');
                wrapper.className = 'st-key-velocity_notify_enable stButton';
                wrapper.innerHTML = '<button>Enable browser notifications</button>';
                document.getElementById('app').appendChild(wrapper);
            }""")
        await self.page.wait_for_timeout(250)
        await self.page.click(".st-key-velocity_notify_enable button")
        await self.page.wait_for_timeout(250)
        self.assertEqual(await self.page.evaluate("window.__asked"), 1)
        # The check that arrived while permission was missing is still announced.
        self.assertEqual([item["tag"] for item in await self.shown()], ["run-1"])

    async def test_a_browser_that_refuses_notifications_breaks_nothing(self):
        await self.install("denied")
        await self.marker("run-1")
        self.assertEqual(await self.shown(), [])
        await self.page.evaluate("() => { delete window.Notification; }")
        await self.page.evaluate(SCRIPT)  # Reinstalling without the API must not throw.
        await self.marker("run-2")
        self.assertEqual(await self.page.evaluate("window.__shown"), [])


if __name__ == "__main__":
    unittest.main()
