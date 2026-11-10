"""Full browser E2E dashboard tests, frontend script Chromium checks, and module branch coverage."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from playwright.async_api import async_playwright
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR_JS = (ROOT / "modules" / "theme" / "sidebar.js").read_text(encoding="utf-8")
AUDIO_JS = (ROOT / "modules" / "theme" / "audio-feedback.js").read_text(encoding="utf-8")
REVEAL_JS = (ROOT / "modules" / "theme" / "reveal.js").read_text(encoding="utf-8")


@unittest.skipUnless(os.getenv("DASHBOARD_E2E", "1") != "0", "Chromium dashboard E2E disabled")
class DashboardBrowserE2ETests(unittest.TestCase):
    """Real Chromium + Streamlit E2E across all 8 dashboard workspaces, sidebar rail, audio toggle, and sign-out."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.temp = tempfile.TemporaryDirectory(prefix="sensemaker-dashboard-e2e-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.log_path = Path(cls.temp.name) / "streamlit.log"
        cls.log = cls.log_path.open("w", encoding="utf-8")
        cls.addClassCleanup(cls.log.close)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "TMPDIR", "SYSTEMROOT", "PLAYWRIGHT_BROWSERS_PATH"}
        }
        env.update(PYTHONPATH=str(ROOT), VELOCITY_DAILY_CHECKS="0")
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(ROOT / "tests" / "fixtures" / "dashboard_e2e_app.py"),
                "--server.headless=true",
                "--server.address=127.0.0.1",
                f"--server.port={port}",
                "--browser.gatherUsageStats=false",
            ],
            cwd=ROOT,
            env=env,
            stdout=cls.log,
            stderr=subprocess.STDOUT,
        )
        cls.addClassCleanup(cls.stop_server)
        cls.url = f"http://127.0.0.1:{port}"
        for _ in range(150):
            if cls.server.poll() is not None:
                raise RuntimeError(cls.log_path.read_text(encoding="utf-8"))
            try:
                with urlopen(cls.url + "/_stcore/health", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Streamlit E2E server did not start: " + cls.log_path.read_text(encoding="utf-8"))
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(headless=True)
        cls.addClassCleanup(cls.browser.close)

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=5)

    def test_authenticated_dashboard_navigation_rail_audio_toggle_and_sign_out_flow(self):
        from playwright.sync_api import expect

        context = self.browser.new_context(viewport={"width": 1366, "height": 900})
        self.addCleanup(context.close)
        context.route(
            "**/*",
            lambda route: route.continue_() if route.request.url.startswith(self.url + "/") else route.abort(),
        )
        # Theme bootstrap otherwise reloads on first visit, overlapping two
        # Streamlit sessions that patch the same process-local service fixtures.
        context.add_init_script("localStorage.setItem('sensemaker.themeDefault', 'light')")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.set_default_timeout(15000)

        page.goto(self.url)
        nav = page.locator("#sm-workspace-nav")
        expect(nav).to_be_visible()
        expect(page.locator("html")).to_have_class(
            __import__("re").compile(r".*sm-navigation-ready.*")
        )
        expect(page.locator("#sm-sound-toggle")).to_be_visible()
        expect(page.locator("#sm-sound-toggle")).to_have_text("♫ Sound on")

        # Navigate across every workspace via the custom glass sidebar rail.
        for view in [
            "Clean & manage data",
            "Sentiment Analysis",
            "Velocity Tracking",
            "Sentiment Trends",
            "Content recommendations",
            "GenCite",
            "Tutorial",
            "Scrap data",
        ]:
            button = nav.locator(f'button[data-sm-view="{view}"]')
            button.click()
            expect(button).to_have_attribute("aria-current", "page")

        # Collapse and expand the sidebar via toggle button and keyboard Escape.
        toggle = page.locator(".sm-sidebar-toggle")
        expect(toggle).to_have_attribute("aria-expanded", "true")
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(page.locator("html")).to_have_class(
            __import__("re").compile(r".*sm-sidebar-collapsed.*")
        )
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")

        # Toggle browser audio feedback on/off and verify localStorage persistence.
        sound_btn = page.locator("#sm-sound-toggle")
        sound_btn.click()
        expect(sound_btn).to_have_attribute("aria-pressed", "false")
        expect(sound_btn).to_have_text("♫ Sound off")
        self.assertEqual(page.evaluate("localStorage.getItem('crisisradar.sounds')"), "off")
        sound_btn.click()
        expect(sound_btn).to_have_attribute("aria-pressed", "true")
        expect(sound_btn).to_have_text("♫ Sound on")

        # Sign out using the custom sidebar rail sign-out control, then sign back in.
        page.locator(".sm-rail-sign-out").click()
        expect(page.get_by_role("heading", name="Sign in to CrisisRadar")).to_be_visible()
        expect(page.get_by_role("button", name="Sign in as test editor")).to_be_visible()
        page.get_by_role("button", name="Sign in as test editor").click()
        expect(nav).to_be_visible()
        expect(page.get_by_test_id("stException")).to_have_count(0)
        self.assertEqual(errors, [])


class FrontendScriptsBrowserTests(unittest.IsolatedAsyncioTestCase):
    """Real Chromium unit/integration checks for sidebar.js, audio-feedback.js, and reveal.js."""

    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 1024, "height": 768})

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def test_sidebar_script_navigation_keyboard_dialog_escape_and_cleanup(self):
        html = """<!doctype html><html><body>
          <div class="sm-sidebar">
            <button class="sm-rail-button sm-sidebar-toggle" type="button"></button>
            <nav id="sm-workspace-nav">
              <button class="sm-rail-button" type="button" data-sm-view="Scrap data">Scrape</button>
              <button class="sm-rail-button" type="button" data-sm-view="Velocity Tracking">Velocity</button>
              <button class="sm-rail-button" type="button" data-sm-view="Tutorial">Tutorial</button>
            </nav>
            <button class="sm-rail-button sm-rail-sign-out" type="button">Sign out</button>
          </div>
          <div class="st-key-sensemaker_sign_out"><button id="real-sign-out" type="button">Hidden Sign out</button></div>
          <div data-testid="stMain" style="height:200px;overflow:auto">
            <div style="height:600px"></div>
            <div class="st-key-sm_views">
              <div role="tablist">
                <button role="tab" aria-selected="true" aria-controls="panel-0">Scrap data</button>
                <button role="tab" aria-selected="false" aria-controls="panel-1">Velocity Tracking</button>
                <button role="tab" aria-selected="false" aria-controls="panel-2">Tutorial</button>
              </div>
            </div>
          </div>
        </body></html>"""
        await self.page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        await self.page.goto("https://crisisradar.test/")
        await self.page.evaluate(
            """() => {
              window.__signedOut = 0;
              document.getElementById('real-sign-out').addEventListener('click', () => { window.__signedOut += 1; });
              document.querySelectorAll('[role="tab"]').forEach(tab => {
                tab.addEventListener('click', () => {
                  document.querySelectorAll('[role="tab"]').forEach(t => t.setAttribute('aria-selected', String(t === tab)));
                });
              });
            }"""
        )
        await self.page.evaluate(SIDEBAR_JS)

        # Clicking a rail item activates the tab, updates aria-current, and scrolls stMain to top.
        await self.page.evaluate("document.querySelector('[data-testid=\"stMain\"]').scrollTop = 120")
        await self.page.locator('button[data-sm-view="Velocity Tracking"]').click()
        await self.page.wait_for_function(
            "document.querySelector('button[data-sm-view=\"Velocity Tracking\"]').getAttribute('aria-current') === 'page'"
        )
        self.assertEqual(await self.page.evaluate("document.querySelector('[data-testid=\"stMain\"]').scrollTop"), 0)

        # ArrowDown, ArrowUp, End, Home move focus among rail items.
        await self.page.locator('button[data-sm-view="Scrap data"]').focus()
        await self.page.keyboard.press("ArrowDown")
        self.assertEqual(await self.page.evaluate("document.activeElement.dataset.smView"), "Velocity Tracking")
        await self.page.keyboard.press("End")
        self.assertEqual(await self.page.evaluate("document.activeElement.dataset.smView"), "Tutorial")
        await self.page.keyboard.press("Home")
        self.assertEqual(await self.page.evaluate("document.activeElement.dataset.smView"), "Scrap data")
        await self.page.keyboard.press("ArrowUp")
        self.assertEqual(await self.page.evaluate("document.activeElement.dataset.smView"), "Tutorial")

        # Escape inside an open velocity notepad dialog clicks its Close button instead of collapsing the sidebar.
        await self.page.evaluate(
            """() => {
              const dialog = document.createElement('div');
              dialog.setAttribute('role', 'dialog');
              dialog.innerHTML = '<div class="st-key-sm_velocity_modal"></div><button aria-label="Close" id="dlg-close"></button>';
              window.__dialogClosed = 0;
              dialog.querySelector('#dlg-close').addEventListener('click', () => { window.__dialogClosed += 1; dialog.remove(); });
              document.body.appendChild(dialog);
            }"""
        )
        await self.page.keyboard.press("Escape")
        self.assertEqual(await self.page.evaluate("window.__dialogClosed"), 1)
        self.assertFalse(await self.page.evaluate("document.documentElement.classList.contains('sm-sidebar-collapsed')"))

        # Escape with no dialog collapses the sidebar and focuses the toggle button.
        await self.page.keyboard.press("Escape")
        self.assertTrue(await self.page.evaluate("document.documentElement.classList.contains('sm-sidebar-collapsed')"))

        # Sign-out rail button triggers the hidden Streamlit sign-out button, and cleanup disconnects handlers.
        await self.page.locator(".sm-rail-sign-out").click()
        self.assertEqual(await self.page.evaluate("window.__signedOut"), 1)
        await self.page.evaluate("window.smSidebarCleanup()")

    async def test_audio_feedback_script_synthesis_coalescing_dedup_and_mute(self):
        await self.page.route(
            "**/*",
            lambda route: route.fulfill(status=200, content_type="text/html", body="<body><div id='app'></div></body>"),
        )
        await self.page.goto("https://crisisradar.test/")
        await self.page.evaluate(
            """() => {
              window.__notes = [];
              class FakeAudioContext {
                constructor() { this.state = 'suspended'; this.currentTime = 0; this.destination = {}; }
                resume() { this.state = 'running'; return Promise.resolve(); }
                createGain() {
                  return {
                    gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} },
                    connect() {}, disconnect() {}
                  };
                }
                createOscillator() {
                  const osc = {
                    type: 'sine',
                    frequency: { value: 0 },
                    connect() {}, disconnect() {},
                    start() { window.__notes.push(osc.frequency.value); },
                    stop() { if (osc.onended) osc.onended(); }
                  };
                  return osc;
                }
              }
              window.AudioContext = FakeAudioContext;
            }"""
        )
        await self.page.evaluate(AUDIO_JS)
        # Unlock audio via pointerdown.
        await self.page.mouse.click(5, 5)

        # Coalescing: success + error emitted together plays the 2-note error sound once.
        await self.page.evaluate(
            """() => {
              const app = document.getElementById('app');
              app.innerHTML = '<span data-sm-sound="success" data-sm-sound-id="evt-1" hidden></span>'
                            + '<span data-sm-sound="error" data-sm-sound-id="evt-2" hidden></span>';
            }"""
        )
        await self.page.wait_for_function("window.__notes.length === 2")
        self.assertEqual(await self.page.evaluate("window.__notes"), [349.23, 261.63])

        # Toggle mute off then back on (re-enabling plays the 3-note success triad after cooldown).
        await self.page.evaluate("performance.now = () => 5000")
        await self.page.locator("#sm-sound-toggle").click()
        self.assertEqual(await self.page.evaluate("localStorage.getItem('crisisradar.sounds')"), "off")
        await self.page.locator("#sm-sound-toggle").click()
        self.assertEqual(await self.page.evaluate("localStorage.getItem('crisisradar.sounds')"), "on")
        await self.page.wait_for_function("window.__notes.length === 5")
        self.assertEqual(await self.page.evaluate("window.__notes.slice(2)"), [523.25, 659.25, 783.99])

    async def test_reveal_script_scroll_and_intersection_observer(self):
        html = """<body>
          <div data-testid="stMain" style="height:100px;overflow:auto">
            <div class="sm-reveal" style="height:40px">Card 1</div>
            <div style="height:400px">Spacer</div>
          </div>
        </body>"""
        await self.page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        await self.page.goto("https://crisisradar.test/")
        await self.page.evaluate(REVEAL_JS)
        await self.page.wait_for_function("document.querySelector('.sm-reveal').classList.contains('sm-in')")
        await self.page.evaluate(
            """() => {
              const main = document.querySelector('[data-testid="stMain"]');
              main.scrollTop = 25;
              main.dispatchEvent(new Event('scroll', { bubbles: true }));
              const dynamic = document.createElement('div');
              dynamic.className = 'sm-reveal';
              main.appendChild(dynamic);
            }"""
        )
        self.assertTrue(await self.page.evaluate("document.documentElement.classList.contains('sm-scrolled')"))


class EntrypointAndScriptCoverageTests(unittest.TestCase):
    """In-process unit tests for hosted.py and scripts/save_storage_state.py."""

    def test_hosted_server_registers_component_and_starts_scheduler_safely(self):
        import hosted

        registry = MagicMock()
        instance = SimpleNamespace(component_registry=registry)
        with patch("hosted.Server.__init__", return_value=None), patch("hosted.get_instance", return_value=instance):
            hosted.HostedServer("dashboard.py", False)
        registry.register_component.assert_called_once()

        with patch("modules.velocity_schedule.ensure_scheduler_started") as ensure:
            hosted._start_daily_velocity_checks()
            ensure.assert_called_once()

        with patch("modules.velocity_schedule.ensure_scheduler_started", side_effect=RuntimeError("boom")), patch.object(
            hosted.logging.getLogger("hosted"), "exception"
        ) as log_exc:
            hosted._start_daily_velocity_checks()
            log_exc.assert_called_once()

        with patch("hosted._start_daily_velocity_checks") as start_checks, patch(
            "hosted.bootstrap.load_config_options"
        ) as load_opts, patch("hosted.bootstrap.run") as run_boot, patch.dict(os.environ, {"PORT": "9090"}):
            hosted.main()
        start_checks.assert_called_once()
        load_opts.assert_called_once()
        run_boot.assert_called_once()
        self.assertEqual(load_opts.call_args[0][0]["server.port"], 9090)

    def test_save_storage_state_script_main(self):
        from scripts import save_storage_state

        with patch("scripts.save_storage_state.load_environment") as load_env, patch(
            "scripts.save_storage_state.facebook_storage_path", return_value=Path("/tmp/fb-state.json")
        ), patch("scripts.save_storage_state.capture_facebook_session") as capture:
            save_storage_state.main()
        load_env.assert_called_once()
        capture.assert_called_once_with(Path("/tmp/fb-state.json"))


class UIAudioAndSharingCoverageTests(unittest.TestCase):
    """Unit and AppTest checks for modules/ui.py, modules/audio_feedback.py, and velocity sharing UI."""

    def test_ui_helpers_and_theme_render_all_states(self):
        from modules import ui

        with patch.object(ui.st, "html") as st_html, patch.object(ui.components, "html") as comp_html, patch.object(
            ui.st, "markdown"
        ) as st_md, patch.object(ui.st.sidebar, "markdown") as side_md:
            ui.apply_theme()
            st_html.assert_called_once()
            comp_html.assert_called_once()

            ui.hero()
            ui.sign_in_gate()
            ui.account_badge("editor@example.com")
            self.assertEqual(side_md.call_count, 1)

            self.assertIn("Risk <b>?</b>", ui._risk("not-a-number"))
            self.assertIn("sm-risk--low", ui._risk(2))
            self.assertIn("sm-risk--medium", ui._risk(6))
            self.assertIn("sm-risk--high", ui._risk(9))

            ui.analysis_cards(
                [
                    {
                        "sentiment": "Anger",
                        "risk_score": 9,
                        "topics": "boycott, border",
                        "url": "https://example.com/post/1",
                        "translation": "Translated text",
                        "raw_text_preview": "Original text",
                    },
                    {
                        "sentiment": "Other",
                        "risk_score": None,
                        "topics": "",
                        "url": "javascript:alert(1)",
                        "translation": "",
                    },
                ]
            )
            rendered_grid = st_md.call_args[0][0]
            self.assertIn("sm-tone-anger", rendered_grid)
            self.assertIn("sm-tone-unknown", rendered_grid)
            self.assertIn("No translation returned.", rendered_grid)
            self.assertNotIn("javascript:alert", rendered_grid)

    def test_audio_feedback_validates_kind_and_replays_session_marker(self):
        from modules import audio_feedback

        with self.assertRaises(ValueError):
            audio_feedback.notify_sound("invalid-kind")

        state: dict = {}
        with patch.object(audio_feedback.st, "session_state", state), patch.object(
            audio_feedback.st, "html"
        ) as st_html, patch.object(audio_feedback.components, "html") as comp_html:
            audio_feedback.notify_sound("success")
            self.assertEqual(state["audio_feedback_event"]["kind"], "success")
            audio_feedback.install_audio_feedback()
            comp_html.assert_called_once()
            self.assertEqual(st_html.call_count, 2)

    def test_velocity_sharing_ui_and_store_error_branches(self):
        from modules import velocity_sharing, velocity_sharing_ui

        with patch("modules.auth.allowed_emails", return_value={"editor@example.com"}):
            with self.assertRaises(ValueError):
                velocity_sharing.set_sharing(MagicMock(), "editor@example.com", "invalid-id", True)
        with self.assertRaises(ValueError):
            velocity_sharing.share_url("https://app.example.com/", "bad-token")

        card = {"id": "a" * 32, "name": "Card", "share_token": "b" * 64}
        with patch.object(velocity_sharing_ui.st, "expander", return_value=MagicMock()), patch.object(
            velocity_sharing_ui.st, "caption"
        ), patch.object(velocity_sharing_ui.st, "code") as st_code, patch.object(
            velocity_sharing_ui.st, "link_button"
        ) as st_link, patch.object(
            velocity_sharing_ui.st, "button", return_value=True
        ), patch.object(
            velocity_sharing_ui.st, "error"
        ) as st_error, patch(
            "modules.velocity_sharing.share_url", return_value="https://app.example.com/?velocity_share=" + "b" * 64
        ), patch(
            "modules.database.init_db", return_value=MagicMock()
        ):
            with patch("modules.velocity_sharing.set_sharing", side_effect=PermissionError("Denied")):
                self.assertIsNone(velocity_sharing_ui.render_share_controls("editor@example.com", card))
            st_code.assert_called_once()
            st_link.assert_called_once()
            st_error.assert_called_with("Denied")

            with patch("modules.velocity_sharing.set_sharing", side_effect=RuntimeError("DB down")):
                self.assertIsNone(velocity_sharing_ui.render_share_controls("editor@example.com", card))
            st_error.assert_called_with("Sharing could not be updated. Try again.")

        with patch.object(velocity_sharing_ui.st, "html"), patch.object(
            velocity_sharing_ui.st, "caption"
        ), patch.object(velocity_sharing_ui.st, "button"), patch.object(
            velocity_sharing_ui.st, "error"
        ) as page_err, patch(
            "modules.database.init_db", return_value=MagicMock()
        ), patch(
            "modules.velocity_sharing.load_shared_card", side_effect=RuntimeError("Timeout")
        ):
            velocity_sharing_ui.render_shared_card_page("b" * 64)
            page_err.assert_called_once_with("This card could not be loaded. Try refreshing in a moment.")


class ScraperAndVelocityScraperCoverageTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for uncovered branches in modules/scraper.py and modules/velocity_scraper.py."""

    async def test_scraper_browser_fallbacks_settling_and_url_normalization(self):
        from modules import scraper

        # URL normalization with allowed query parameters
        normalized = scraper._normalize_facebook_url(
            "/permalink.php?story_fbid=111&id=222&utm_source=share", "https://www.facebook.com"
        )
        self.assertEqual(normalized, "https://www.facebook.com/permalink.php?id=222&story_fbid=111")

        # _launch_browser_with_fallback: direct, fallback to channel="chrome", and unrelated error
        pw = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value="browser")))
        self.assertEqual(await scraper._launch_browser_with_fallback(pw, True), "browser")

        pw_fallback = SimpleNamespace(
            chromium=SimpleNamespace(
                launch=AsyncMock(side_effect=[RuntimeError("Executable doesn't exist"), "chrome-browser"])
            )
        )
        self.assertEqual(await scraper._launch_browser_with_fallback(pw_fallback, False), "chrome-browser")

        pw_fail = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(side_effect=RuntimeError("Other crash"))))
        with self.assertRaises(RuntimeError):
            await scraper._launch_browser_with_fallback(pw_fail, True)

        # _click_expand_controls exception resilience
        bad_btn = SimpleNamespace(evaluate=AsyncMock(side_effect=RuntimeError("detached")), click=AsyncMock())
        buttons = SimpleNamespace(count=AsyncMock(return_value=1), nth=lambda _i: bad_btn)
        page = SimpleNamespace(
            locator=lambda _sel: SimpleNamespace(get_by_role=lambda *a, **k: buttons, count=AsyncMock(return_value=2)),
            evaluate=AsyncMock(side_effect=[RuntimeError("no body"), 500, 500, 500]),
        )
        await scraper._click_expand_controls(page)

        # _get_feed_signal_count & _wait_for_feed_settle
        def locator_side_effect(sel):
            if "article" in sel:
                return SimpleNamespace(count=AsyncMock(side_effect=RuntimeError("err")))
            return SimpleNamespace(count=AsyncMock(return_value=1))

        page.locator = locator_side_effect
        self.assertEqual(await scraper._get_feed_signal_count(page), 5)
        with patch("modules.scraper.asyncio.sleep", new=AsyncMock()):
            await scraper._wait_for_feed_settle(page, rounds=2)

        # _build_emergency_fallback
        fb = scraper._build_emergency_fallback("https://example.com", reason="timeout")
        self.assertIn("Reason: timeout", fb["raw_text"])

        # _goto_with_resilient_wait all 3 tiers
        p1 = SimpleNamespace(goto=AsyncMock(), wait_for_load_state=AsyncMock())
        await scraper._goto_with_resilient_wait(p1, "https://example.com")
        self.assertEqual(p1.goto.await_count, 1)

        p2 = SimpleNamespace(
            goto=AsyncMock(side_effect=[RuntimeError("dom fail"), None]),
            wait_for_load_state=AsyncMock(),
        )
        await scraper._goto_with_resilient_wait(p2, "https://example.com")
        self.assertEqual(p2.goto.await_count, 2)

        p3 = SimpleNamespace(
            goto=AsyncMock(side_effect=[RuntimeError("dom fail"), RuntimeError("load fail"), None]),
            wait_for_load_state=AsyncMock(),
            wait_for_timeout=AsyncMock(),
        )
        await scraper._goto_with_resilient_wait(p3, "https://example.com")
        self.assertEqual(p3.goto.await_count, 3)
        p3.wait_for_timeout.assert_awaited_once_with(2_000)

    async def test_scrape_group_reddit_persist_local_facebook_and_non_facebook_branches(self):
        from modules import scraper
        from modules.facebook_session import FacebookLoginError

        # Reddit branch with async persist_cb and batch_warning skip
        persisted = []

        async def async_persist(row):
            persisted.append(row["url"])

        with patch(
            "modules.scraper.scrape_reddit",
            new=AsyncMock(
                return_value=[
                    {"source_type": "batch_warning", "url": "https://www.reddit.com/r/cambodia"},
                    {"source_type": "reddit_post", "url": "https://www.reddit.com/r/cambodia/comments/1"},
                ]
            ),
        ):
            rows = await scraper.scrape_group("https://www.reddit.com/r/cambodia", persist_cb=async_persist)
        self.assertEqual(len(rows), 2)
        self.assertEqual(persisted, ["https://www.reddit.com/r/cambodia/comments/1"])

        # Hosted Facebook without facebook_owner raises FacebookLoginError
        with patch.dict(os.environ, {"SENSEMAKER_HOSTED": "1"}):
            with self.assertRaises(FacebookLoginError):
                await scraper.scrape_group("https://www.facebook.com/groups/test")

        # Local Facebook branch with adaptive stop, page-level fallback, async persist_cb, and session save failure resilience
        fake_page = SimpleNamespace(
            mouse=SimpleNamespace(wheel=AsyncMock()),
            evaluate=AsyncMock(return_value="UA"),
        )
        fake_context = SimpleNamespace(new_page=AsyncMock(return_value=fake_page), close=AsyncMock())
        fake_browser = SimpleNamespace(new_context=AsyncMock(return_value=fake_context), close=AsyncMock())
        fake_pw_cm = AsyncMock()
        fake_pw_cm.__aenter__.return_value = SimpleNamespace()

        with patch.dict(os.environ, {"SENSEMAKER_HOSTED": "0"}), patch(
            "modules.scraper.async_playwright", return_value=fake_pw_cm
        ), patch("modules.scraper.launch_facebook_browser", new=AsyncMock(return_value=fake_browser)), patch(
            "modules.scraper.facebook_storage_path", return_value=Path("/tmp/fb.json")
        ), patch("modules.scraper.facebook_state_revision", return_value=(1, 1)), patch(
            "modules.scraper.facebook_context_options", return_value={}
        ), patch("modules.scraper._goto_with_resilient_wait", new=AsyncMock()), patch(
            "modules.scraper._click_expand_controls", new=AsyncMock()
        ), patch("modules.scraper._harvest_visible_posts", new=AsyncMock(return_value=[])), patch(
            "modules.scraper._wait_for_feed_settle", new=AsyncMock()
        ), patch(
            "modules.scraper.save_facebook_session", new=AsyncMock(side_effect=RuntimeError("disk full"))
        ):
            res = await scraper.scrape_group(
                "https://www.facebook.com/groups/test",
                persist_cb=async_persist,
                scroll_min=1,
                scroll_max=5,
            )
        self.assertEqual(res[0]["source_type"], "batch_warning")

        # Non-Facebook branch with PLAYWRIGHT_STORAGE_STATE and exception fallback
        with patch.dict(os.environ, {"PLAYWRIGHT_STORAGE_STATE": "/tmp/state.json"}), patch(
            "modules.scraper.async_playwright", return_value=fake_pw_cm
        ), patch("modules.scraper._launch_browser_with_fallback", new=AsyncMock(return_value=fake_browser)), patch(
            "modules.scraper._goto_with_resilient_wait", new=AsyncMock(side_effect=RuntimeError("nav error"))
        ):
            err_res = await scraper.scrape_group("https://example.org/feed", headless=True)
        self.assertEqual(err_res[0]["source_type"], "emergency_fallback")

    async def test_velocity_scraper_local_browser_and_error_fallbacks(self):
        from modules import velocity_scraper
        from modules.facebook_session import FacebookLoginError, FacebookSession

        # read_post_comments expansion click error
        more_btn = SimpleNamespace(
            count=AsyncMock(return_value=1),
            first=SimpleNamespace(click=AsyncMock(side_effect=RuntimeError("click failed"))),
        )
        comment_page = SimpleNamespace(
            evaluate=AsyncMock(
                side_effect=[
                    {"matched": True, "login_required": False},
                    [{"id": "c1", "text": "First comment"}],
                ]
            ),
            locator=lambda _s: SimpleNamespace(get_by_role=lambda *a, **k: more_btn),
            wait_for_timeout=AsyncMock(),
        )
        sample = await velocity_scraper.read_post_comments(comment_page, "https://www.facebook.com/p/posts/1", 5)
        self.assertEqual(len(sample["comment_sample"]), 1)

        # collect_engagement local disconnected raises FacebookLoginError
        check_page = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close err")))
        ctx = SimpleNamespace(new_page=AsyncMock(return_value=check_page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=ctx), close=AsyncMock())
        pw_cm = AsyncMock()
        pw_cm.__aenter__.return_value = SimpleNamespace()

        with patch("modules.velocity_scraper.async_playwright", return_value=pw_cm), patch(
            "modules.velocity_scraper.launch_facebook_browser", new=AsyncMock(return_value=browser)
        ), patch("modules.velocity_scraper.facebook_context_options", return_value={}), patch(
            "modules.velocity_scraper._profile_session", new=AsyncMock(return_value=FacebookSession("disconnected"))
        ):
            with self.assertRaises(FacebookLoginError):
                await velocity_scraper.collect_engagement(
                    "editor@example.com", [], hosted=False, progress=lambda *a: None, partial=lambda *a: None
                )

        # collect_engagement local connected with comment failure & post check failure
        work_page = SimpleNamespace(close=AsyncMock())
        ctx.new_page = AsyncMock(return_value=work_page)
        with patch("modules.velocity_scraper.async_playwright", return_value=pw_cm), patch(
            "modules.velocity_scraper.launch_facebook_browser", new=AsyncMock(return_value=browser)
        ), patch("modules.velocity_scraper.facebook_context_options", return_value={}), patch(
            "modules.velocity_scraper._profile_session", new=AsyncMock(return_value=FacebookSession("connected", "1"))
        ), patch(
            "modules.velocity_scraper.read_post_engagement",
            new=AsyncMock(
                side_effect=[
                    {"reactions": 5, "comments": 2, "engagement": 7, "outcome": "ok", "resolved_url": "https://www.facebook.com/p/posts/1"},
                    RuntimeError("unexpected failure"),
                ]
            ),
        ), patch(
            "modules.velocity_scraper.read_post_comments", new=AsyncMock(side_effect=RuntimeError("comment error"))
        ):
            results = await velocity_scraper.collect_engagement(
                "editor@example.com",
                [
                    {"id": "p1", "url": "https://www.facebook.com/p/posts/1"},
                    {"id": "p2", "url": "https://www.facebook.com/p/posts/2"},
                ],
                hosted=False,
                progress=lambda *a: None,
                partial=lambda *a: None,
            )
        self.assertEqual(results[0]["comment_collection"]["status"], "error")
        self.assertEqual(results[1]["outcome"], "error")


class RemoteFacebookAndSessionCoverageTests(unittest.IsolatedAsyncioTestCase):
    """Coverage for modules/facebook_remote.py and modules/facebook_session.py edge cases."""

    async def test_facebook_remote_jobs_release_and_context_edges(self):
        from modules import facebook_remote as remote
        from modules.facebook_session import FacebookLoginError, FacebookSession

        # _record and unconfigured remote_facebook_session
        with patch("modules.facebook_remote._profile_ref") as pref:
            pref.return_value.get.return_value.to_dict.return_value = {"context_id": "ctx-1"}
            self.assertEqual(remote._record("editor@example.com"), {"context_id": "ctx-1"})
        with patch("modules.facebook_remote.remote_facebook_configured", return_value=False):
            self.assertEqual(remote.remote_facebook_session("editor@example.com").status, "disconnected")

        # forget_login and start_login thread runner (both success and error + expired cleanup)
        owner = remote.library_owner_id("editor@example.com")
        remote._jobs[(owner, "old-job")] = remote.LoginJob(status="complete", finished_at=1)
        remote.forget_login("editor@example.com", "old-job")
        self.assertIsNone(remote.login_job("editor@example.com", "old-job"))

        remote._jobs[(owner, "expired")] = remote.LoginJob(status="complete", finished_at=1)
        with patch("modules.facebook_remote._login", new=AsyncMock()):
            jid = remote.start_login("editor@example.com", check_only=True)
            for _ in range(50):
                job = remote.login_job("editor@example.com", jid)
                if job and job.status == "complete":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(remote.login_job("editor@example.com", jid).status, "complete")
            remote.forget_login("editor@example.com", jid)

        with patch("modules.facebook_remote._login", new=AsyncMock(side_effect= FacebookLoginError("denied"))):
            jid2 = remote.start_login("editor@example.com", check_only=True)
            for _ in range(50):
                job2 = remote.login_job("editor@example.com", jid2)
                if job2 and job2.status == "error":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(remote.login_job("editor@example.com", jid2).error, "denied")
            remote.forget_login("editor@example.com", jid2)

        # _safe_error on 429 rate limit
        rate_exc = RuntimeError("quota")
        rate_exc.status_code = 429
        self.assertIn("usage limit", remote._safe_error(rate_exc))

        # _acquire and _release connected/disconnected
        ref = MagicMock()
        ref.get.return_value.to_dict.return_value = {"context_id": "ctx-1", "lease": "L1"}
        tx = MagicMock()
        with patch("modules.facebook_remote._ensure_profile", return_value="ctx-1"), patch(
            "modules.facebook_remote._profile_ref", return_value=ref
        ), patch("modules.facebook_remote.firestore.transactional", side_effect=lambda f: f), patch(
            "modules.facebook_remote.init_db", return_value=SimpleNamespace(transaction=lambda: tx)
        ):
            cid, lease = remote._acquire("editor@example.com", 60)
            self.assertEqual(cid, "ctx-1")
            ref.get.return_value.to_dict.return_value = {"context_id": "ctx-1", "lease": lease}
            remote._release("editor@example.com", lease, FacebookSession("connected", "100", "User"))
            remote._release("editor@example.com", lease, FacebookSession("disconnected"))

        # Unconfigured remote_facebook_context
        with patch("modules.facebook_remote.remote_facebook_configured", return_value=False):
            with self.assertRaises(FacebookLoginError):
                async with remote.remote_facebook_context(SimpleNamespace(), "editor@example.com"):
                    pass

    def test_facebook_session_edge_cases(self):
        from modules import facebook_session as fb_sess

        with tempfile.TemporaryDirectory() as tmp:
            bad_file = Path(tmp) / "bad.json"
            bad_file.write_text("{not-json", encoding="utf-8")
            self.assertEqual(fb_sess.facebook_context_options(str(bad_file)), {})
            self.assertEqual(fb_sess.saved_facebook_user_id(str(bad_file)), "")
            self.assertEqual(fb_sess.check_facebook_session(str(bad_file)).status, "disconnected")

            missing = Path(tmp) / "missing.json"
            fb_sess._mark_login_required(str(missing), (1, 1))

        self.assertEqual(
            fb_sess.facebook_user_id(
                [
                    {"domain": "example.com", "name": "c_user", "value": "100"},
                    {"domain": ".facebook.com", "name": "c_user", "value": "100", "expires": "not-a-float"},
                ]
            ),
            "",
        )


class SocialAndNewsCollectorsCoverageTests(unittest.TestCase):
    """Coverage for transport and parser branches in modules/reddit.py, modules/x.py, modules/google_news.py, and modules/websites.py."""

    def test_reddit_transport_and_record_edge_cases(self):
        from urllib.request import Request
        from modules import reddit

        with self.assertRaises(reddit.RedditError):
            reddit.parse_reddit_source("https://www.reddit.com/r/cambodia/search?q=")

        req = Request("https://oauth.reddit.com/r/cambodia/new")
        with patch("modules.reddit.reddit_configured", return_value=True), patch(
            "modules.reddit.get_setting", return_value="val"
        ):
            client = reddit.RedditClient(deadline=time.monotonic() - 1)
            with patch("modules.reddit._next_request", 0):
                with self.assertRaises(reddit.RedditError):
                    client._request(req)

            with patch("modules.reddit._next_request", time.monotonic() + 30):
                with self.assertRaises(reddit.RedditError):
                    reddit.RedditClient(deadline=time.monotonic() + 60)._request(req)

            for code in (401, 404, 500):
                err = HTTPError("https://oauth.reddit.com", code, "err", {}, io.BytesIO(b"{}"))
                with patch("modules.reddit._next_request", 0), patch("modules.reddit.urlopen", side_effect=err):
                    with self.assertRaises(reddit.RedditError):
                        reddit.RedditClient(deadline=time.monotonic() + 30)._request(req)

            with patch("modules.reddit._next_request", 0), patch("modules.reddit.urlopen", side_effect=URLError("down")):
                with self.assertRaises(reddit.RedditError):
                    reddit.RedditClient(deadline=time.monotonic() + 30)._request(req)

        with self.assertRaises(reddit.RedditError):
            reddit._children("not-a-dict")
        with self.assertRaises(reddit.RedditError):
            reddit._children({"data": {"children": "not-a-list"}})

        self.assertIsNone(reddit._record({"subreddit": "other", "id": "abc"}, "t3"))
        self.assertIsNone(reddit._record({"subreddit": "cambodia", "id": "bad-id!"}, "t3"))
        self.assertIsNone(reddit._record({"subreddit": "cambodia", "id": "abc", "title": ""}, "t3"))
        self.assertIsNone(reddit._record({"subreddit": "cambodia", "id": "abc", "body": "hi"}, "t1", parent=None))

    def test_x_transport_and_query_edge_cases(self):
        from modules import x

        with self.assertRaises(x.XError):
            x.build_post_query("a" * 401)
        with patch("modules.x.MAX_QUERY", 600):
            with self.assertRaises(x.XError):
                x.build_post_query("a" * 505)

        with patch("modules.x.x_configured", return_value=True), patch("modules.x.get_setting", return_value="token"):
            with patch("modules.x._next_request", time.monotonic() + 20):
                with self.assertRaises(x.XError):
                    x.XClient(deadline=time.monotonic() + 60)._request({})
            with patch("modules.x._next_request", 0):
                with self.assertRaises(x.XError):
                    x.XClient(deadline=time.monotonic() - 1)._request({})

            for code in (429, 402, 400, 500):
                err = HTTPError("https://api.x.com", code, "err", {}, io.BytesIO(b"{}"))
                with patch("modules.x._next_request", 0), patch("modules.x.urlopen", side_effect=err):
                    with self.assertRaises(x.XError):
                        x.XClient(deadline=time.monotonic() + 30)._request({})

            with patch("modules.x._next_request", 0), patch("modules.x.urlopen", side_effect=URLError("down")):
                with self.assertRaises(x.XError):
                    x.XClient(deadline=time.monotonic() + 30)._request({})

        self.assertIsNone(x._reply_total({"public_metrics": {"reply_count": "bad"}}))
        self.assertIsNone(x._record("not-dict", query="q", category="c", source_type="x_post"))
        self.assertIsNone(x._record({"id": "not-digits"}, query="q", category="c", source_type="x_post"))
        self.assertEqual(
            x._parent_context(
                {"referenced_tweets": [{"type": "replied_to", "id": "99"}]},
                {"url": "u", "raw_text": "r", "x_id": "1"},
                {"99": {"id": "99", "text": "Parent"}},
            ),
            {"url": "https://x.com/i/status/99", "raw_text": "Parent"},
        )
        with self.assertRaises(x.XError):
            x.collect_x({}, client=MagicMock())

    def test_google_news_and_websites_edge_cases(self):
        from modules import google_news, websites

        resp = MagicMock()
        resp.__enter__.return_value.read.return_value = b"<rss></rss>"
        with patch("modules.google_news.urlopen", return_value=resp):
            self.assertEqual(google_news.fetch_feed("https://news.google.com/rss"), b"<rss></rss>")

        http_err = HTTPError("https://news.google.com/rss", 503, "unavailable", {}, io.BytesIO(b""))
        with patch("modules.google_news.urlopen", side_effect=http_err):
            with self.assertRaises(google_news.NewsError):
                google_news.fetch_feed("https://news.google.com/rss")

        with patch("modules.google_news.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(google_news.NewsError):
                google_news.fetch_feed("https://news.google.com/rss")

        self.assertEqual(google_news._canonical_link("ftp://invalid"), "")
        self.assertEqual(google_news._published("not-a-date"), "")

        title, text, published, truncated = websites.extract_page(
            b'<html><head><title>Article</title><meta property="article:published_time" content="2026-04-01T00:00:00Z"/></head>'
            b"<body><main><p>Line one<br/><span/>Line two</p></main></body></html>",
            "text/html; charset=unknown-charset-xyz",
        )
        self.assertEqual(title, "Article")
        self.assertIn("Line one", text)
        self.assertEqual(published, "2026-04-01T00:00:00Z")
        self.assertFalse(truncated)


class AIProcessorSignalsAndDatabaseCoverageTests(unittest.TestCase):
    """Coverage for modules/signals.py, modules/gemini.py, modules/openai.py, modules/processor.py, and modules/database.py."""

    def test_signals_normalize_analysis_validation_branches(self):
        from modules import signals

        post = {"_id": "1", "raw_text": "Thai brand discussion", "comments": ["Comment text"]}
        with self.assertRaises(ValueError):
            signals.normalize_analysis({"monitor": None}, post, [], "model")
        with self.assertRaises(ValueError):
            signals.normalize_analysis({"monitor": {"signal_type": "bad", "thailand_relevance": "explicit"}}, post, [], "model")
        with self.assertRaises(ValueError):
            signals.normalize_analysis(
                {"monitor": {"signal_type": "none", "thailand_relevance": "explicit", "evidence": "bad"}}, post, [], "model"
            )

        out = signals.normalize_analysis(
            {
                "sentiment": "InvalidSentiment",
                "risk_score": "not-an-int",
                "monitor": {
                    "thailand_relevance": "watchlist",
                    "signal_type": "none",
                    "target_kind": "invalid_kind",
                    "target_names": "not-a-list",
                    "evidence": [
                        "not-a-dict",
                        {"source": "raw_text", "quote": "Thai", "purpose": "relevance"},
                    ],
                },
            },
            post,
            ["Oyoshi"],
            "gemini",
        )
        self.assertEqual(out["sentiment"], "Neutral")
        self.assertEqual(out["risk_score"], 1)
        self.assertEqual(out["monitor"]["thailand_relevance"], "unclear")
        self.assertEqual(out["monitor"]["target_kind"], "unclear")

    def test_gemini_and_openai_validation_branches(self):
        from modules import gemini, openai

        with self.assertRaises(gemini.GeminiError):
            gemini.normalize_model_name("models/bad model name!")
        with self.assertRaises(gemini.GeminiError):
            gemini.list_text_models("   ")

        with patch("modules.gemini.urlopen", side_effect=URLError("down")):
            with self.assertRaises(gemini.GeminiError):
                gemini.list_text_models("key")

        resp = MagicMock()
        resp.__enter__.return_value = io.StringIO('{"models": []}')
        with patch("modules.gemini.urlopen", return_value=resp):
            with self.assertRaises(gemini.GeminiError):
                gemini.list_text_models("key")

        with self.assertRaises(openai.OpenAIError):
            openai.generate_content("prompt", "   ", "gpt-5.4-mini")

        bad_json_resp = MagicMock()
        bad_json_resp.__enter__.return_value = io.StringIO("{bad-json")
        with patch("modules.openai.urlopen", return_value=bad_json_resp):
            with self.assertRaises(openai.OpenAIError):
                openai.generate_content("prompt", "sk-test", "gpt-5.4-mini")

    def test_processor_json_extraction_and_pending_posts_branches(self):
        from modules import processor

        with self.assertRaises(processor.GeminiError):
            processor._generate_content("prompt", "key", processor.AUTO_MODEL)
        with self.assertRaises(ValueError):
            processor._extract_json_object("   ")
        self.assertEqual(processor._extract_json_object("```json\n{\"results\": []}\n```"), {"results": []})
        self.assertEqual(processor._extract_json_object("prefix {\"results\": []} suffix"), {"results": []})
        with self.assertRaises(ValueError):
            processor._extract_json_object("prefix [1, 2, 3] suffix")

        self.assertEqual(processor.analyze_posts([], api_key="key", provider="gemini"), {})
        with self.assertRaises(ValueError):
            processor.analyze_posts([{"_id": ""}], api_key="key", provider="gemini")

        db = MagicMock()
        with patch("modules.processor.get_pending_posts", return_value=[]):
            self.assertEqual(processor.process_pending_posts(db), 0)

        with patch("modules.processor.get_pending_posts", return_value=[{"_id": "doc1", "raw_text": "hello"}]), patch(
            "modules.processor.analyze_posts", side_effect=RuntimeError("API down")
        ), patch("modules.processor.mark_post_processed") as mark_err:
            self.assertEqual(processor.process_pending_posts(db), 1)
            mark_err.assert_called_once_with(db, doc_id="doc1", analysis=None, status="error")

    def test_database_helper_branches(self):
        from modules import database

        doc = MagicMock()
        doc.to_dict.return_value = {"_id": "p1", "status": "pending"}
        db = MagicMock()
        db.collection.return_value.where.return_value.limit.return_value.stream.return_value = [doc]
        self.assertEqual(database.get_pending_posts(db, limit=10), [{"_id": "p1", "status": "pending"}])

        database.mark_post_processed(db, "p1", {"sentiment": "Neutral"}, status="processed")
        db.collection.return_value.document.return_value.update.assert_called_once()

        with self.assertRaises(ValueError):
            database.upsert_post(db, {"raw_text": "no url"})

        database.save_dataset_inclusion(db, [{"_id": "p1", "custom_title": "T"}], {})
        with self.assertRaises(ValueError):
            database.rename_dataset(db, "title:T", {}, "New Title")


class DashboardUnusedHelpersAndAutoCheckTests(unittest.TestCase):
    """Coverage for dashboard.py helper functions (_run_scrape_sync, _risk_color, _auto_check_facebook)."""

    def test_dashboard_risk_color_run_scrape_sync_and_auto_check_facebook(self):
        import dashboard
        from modules.facebook_session import FacebookSession

        self.assertEqual(dashboard._risk_color("invalid"), "#888888")
        self.assertEqual(dashboard._risk_color(9), "#d9534f")
        self.assertEqual(dashboard._risk_color(6), "#f0ad4e")
        self.assertEqual(dashboard._risk_color(2), "#5cb85c")

        with patch("dashboard.scrape_group", new=AsyncMock(return_value=[{"url": "https://example.com"}])):
            self.assertEqual(dashboard._run_scrape_sync("https://example.com", True, 2), [{"url": "https://example.com"}])

        state = {"auth_token": "valid-token"}
        session = FacebookSession("saved", "1001", "Editor")
        with patch("dashboard.IS_CLOUD", True), patch.object(dashboard.st, "session_state", state), patch(
            "modules.auth.allowed_emails", return_value={"editor@example.com"}
        ), patch("modules.auth.verify_access", return_value="editor@example.com"), patch(
            "dashboard.start_login", return_value="check-job-1"
        ) as start_login:
            dashboard._auto_check_facebook("editor@example.com", session)
            self.assertEqual(state["facebook_login_job"], "check-job-1")
            start_login.assert_called_once_with("editor@example.com", check_only=True)

    def test_dashboard_e2e_fixture_apptest_in_process(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(ROOT / "tests" / "fixtures" / "dashboard_e2e_app.py"))
        at.run(timeout=20)
        self.assertEqual(len(at.exception), 0)
        self.assertGreaterEqual(len(at.tabs), 8)


if __name__ == "__main__":
    unittest.main()
