"""Opt-in real-browser checks: WORD_CLOUD_BROWSER_TEST=1 python -m unittest tests.test_word_cloud_browser."""

import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


@unittest.skipUnless(os.getenv("WORD_CLOUD_BROWSER_TEST", "1") != "0", "Chromium integration check disabled")
class WordCloudBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.temp = tempfile.TemporaryDirectory(prefix="sensemaker-cloud-")
        root = Path(__file__).resolve().parents[1]
        script = Path(cls.temp.name) / "app.py"
        script.write_text(f"""
import sys
sys.path.insert(0, {str(root)!r})
import streamlit as st
from dashboard import _render_client_report
st.set_page_config(layout="wide")
words = 'community peace border tourism trade schools families market transport culture dialogue cooperation investment health education opportunity friendship local business visitors growth stability harvest farmers bridges safety youth festival heritage progress jobs rivers shops roads travel support hope calm language exchange development services environment learning partnership respect kindness food nature future'.split()
rows = [{{'_id': 'positive', 'url': 'https://example.com/positive', 'sentiment': 'Joy',
         'translation': 'community community community peace peace ' + ' '.join(words[:28])}},
        {{'_id': 'neutral', 'url': 'https://example.com/neutral', 'sentiment': 'Neutral',
         'translation': 'community peace ' + ' '.join(words[28:])}}]
_render_client_report(rows, 'preview', '2026-11-07', lambda: None)
""", encoding="utf-8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.log = open(Path(cls.temp.name) / "streamlit.log", "w")
        cls.server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(script),
                                       "--server.headless=true", "--server.address=127.0.0.1",
                                       f"--server.port={port}", "--browser.gatherUsageStats=false"],
                                      cwd=root, stdout=cls.log, stderr=subprocess.STDOUT)
        cls.url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with urlopen(cls.url + "/_stcore/health", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Local Streamlit fixture did not start")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.log.close()
        cls.temp.cleanup()

    def open_cloud(self, page):
        from playwright.sync_api import expect
        page.goto(self.url)
        page.get_by_role("tab", name="Word cloud", exact=True).click()
        frame = page.frame_locator("iframe").first
        expect(frame.locator(".word")).to_have_count(50)
        expect(frame.locator(".word").first).to_have_attribute("data-x", re.compile(r"\d+"))
        return frame

    def assert_layout(self, frame):
        result = frame.locator("#cloud").evaluate("""cloud => {
          const boxes = [...cloud.querySelectorAll('.word')].map(word => ({
            x: +word.dataset.x, y: +word.dataset.y, w: word.offsetWidth, h: word.offsetHeight }));
          return { width: cloud.clientWidth, height: cloud.clientHeight,
            overflow: boxes.some(b => b.x < 4 || b.y < 4 || b.x + b.w > cloud.clientWidth - 4 || b.y + b.h > cloud.clientHeight - 4),
            overlap: boxes.some((b, i) => boxes.slice(i + 1).some(c =>
              !(b.x + b.w + 9 <= c.x || c.x + c.w + 9 <= b.x || b.y + b.h + 9 <= c.y || c.y + c.h + 9 <= b.y))) };
        }""")
        self.assertFalse(result["overflow"], result)
        self.assertFalse(result["overlap"], result)
        return result

    def test_selection_drag_pause_mobile_and_keyboard(self):
        from playwright.sync_api import expect
        page = self.browser.new_page(viewport={"width": 1280, "height": 1100})
        self.addCleanup(page.close)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        frame = self.open_cloud(page)
        desktop = self.assert_layout(frame)
        word = frame.get_by_role("button", name="community:")
        # A real pointer entering a moving label pauses the cloud before clicking.
        word.hover(force=True)
        expect(frame.locator(".cloud-card")).to_have_class(re.compile(r".*inspecting.*"))
        word.click()
        expect(frame.get_by_role("heading", name="community", exact=True)).to_be_visible()
        expect(frame.locator(".counts")).to_have_text("5 occurrences across 2 records.")
        expect(frame.get_by_role("link", name="Open source")).to_have_count(2)
        frame.get_by_role("button", name="Pause motion", exact=True).click()
        expect(frame.locator(".cloud-card")).to_have_class(re.compile(r".*paused.*"))
        before = word.get_attribute("data-x")
        box = word.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 35, box["y"] + box["height"] / 2 + 25, steps=5)
        page.mouse.up()
        self.assertNotEqual(word.get_attribute("data-x"), before)
        frame.get_by_role("button", name="Reset layout").click()
        page.wait_for_timeout(150)
        self.assert_layout(frame)
        other = frame.get_by_role("button", name="peace:")
        other.focus()
        other.press("Enter")
        expect(frame.get_by_role("heading", name="peace", exact=True)).to_be_visible()
        artifact_dir = Path(os.getenv("WORD_CLOUD_ARTIFACT_DIR", tempfile.gettempdir()))
        page.screenshot(path=str(artifact_dir / "sensemaker-word-cloud-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(250)
        mobile = self.assert_layout(frame)
        self.assertLess(mobile["width"], desktop["width"])
        expect(frame.locator(".word")).to_have_count(20)
        self.assertGreaterEqual(frame.locator(".word").first.evaluate("b => b.offsetHeight"), 44)
        page.screenshot(path=str(artifact_dir / "sensemaker-word-cloud-mobile.png"), full_page=True)
        frame.get_by_role("button", name="Show all 50").click()
        expect(frame.locator(".word")).to_have_count(50)
        page.wait_for_timeout(150)
        self.assert_layout(frame)
        frame.get_by_role("button", name="Show top 20").click()
        expect(frame.locator(".word")).to_have_count(20)
        page.get_by_role("tab", name="Negative Post Reports (0)", exact=True).click()
        expect(page.get_by_text("No negative or flagged records in this set", exact=False)).to_be_visible()
        page.get_by_role("tab", name="Word cloud", exact=True).click()
        page.wait_for_timeout(250)
        self.assert_layout(frame)
        self.assertEqual(errors, [])

    def test_reduced_motion_and_sentiment_filter_empty_state(self):
        from playwright.sync_api import expect
        context = self.browser.new_context(reduced_motion="reduce")
        self.addCleanup(context.close)
        page = context.new_page()
        frame = self.open_cloud(page)
        expect(frame.get_by_role("button", name="Reduced motion on")).to_be_disabled()
        self.assertEqual(frame.locator(".word").first.evaluate("b => getComputedStyle(b).animationName"), "none")
        page.get_by_text("Cloud settings", exact=True).click()
        sentiment = page.get_by_test_id("stSelectbox").filter(has_text="Sentiment").get_by_role("combobox")
        sentiment.click()
        page.get_by_role("option", name="Negative", exact=True).click()
        expect(page.get_by_text("No words match these settings.", exact=False)).to_be_visible()
        sentiment.click()
        page.get_by_role("option", name="Positive", exact=True).click()
        frame = page.frame_locator("iframe").first
        expect(frame.get_by_role("button", name="community:")).to_be_visible()
        frame.get_by_role("button", name="community:").click()
        expect(frame.locator(".counts")).to_have_text("4 occurrences across 1 record.")
