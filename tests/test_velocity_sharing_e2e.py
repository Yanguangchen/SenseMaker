"""Run: VELOCITY_SHARING_E2E=1 python -m pytest tests/test_velocity_sharing_e2e.py -q.

Real Chromium/Streamlit sessions; only external services use local fixtures.
"""
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


@unittest.skipUnless(os.getenv('VELOCITY_SHARING_E2E', '1') != '0', 'Chromium E2E disabled')
class VelocitySharingE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.temp = tempfile.TemporaryDirectory(prefix='velocity-sharing-e2e-')
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(__file__).resolve().parents[1]
        cls.log_path = Path(cls.temp.name) / 'streamlit.log'
        cls.log = cls.log_path.open('w')
        cls.addClassCleanup(cls.log.close)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        # No inherited service credentials. Prevent loading repository .env via fixture.
        env = {key: value for key, value in os.environ.items()
               if key in {'PATH', 'HOME', 'TMPDIR', 'SYSTEMROOT', 'PLAYWRIGHT_BROWSERS_PATH'}}
        env.update(PYTHONPATH=str(root), VELOCITY_DAILY_CHECKS='0')
        cls.server = subprocess.Popen([
            sys.executable, '-m', 'streamlit', 'run',
            str(root / 'tests/fixtures/velocity_sharing_app.py'),
            '--server.headless=true', '--server.address=127.0.0.1',
            f'--server.port={port}', '--browser.gatherUsageStats=false',
        ], cwd=root, env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        cls.addClassCleanup(cls.stop_server)
        cls.url = f'http://127.0.0.1:{port}'
        for _ in range(150):
            if cls.server.poll() is not None:
                raise RuntimeError(cls.log_path.read_text())
            try:
                with urlopen(cls.url + '/_stcore/health', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Streamlit did not start: ' + cls.log_path.read_text())
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

    def test_share_lifecycle_across_owner_and_anonymous_browser_sessions(self):
        from playwright.sync_api import expect
        owner_context = self.browser.new_context(permissions=['clipboard-read', 'clipboard-write'])
        viewer_context = self.browser.new_context(viewport={'width': 390, 'height': 844})
        self.addCleanup(owner_context.close)
        self.addCleanup(viewer_context.close)
        for context in [owner_context, viewer_context]:
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.url + '/') else route.abort())
        owner, viewer = owner_context.new_page(), viewer_context.new_page()
        errors = []
        for page in [owner, viewer]:
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_default_timeout(15000)
        try:
            owner.goto(self.url)
            owner.get_by_role('button', name='Sign in as test owner').click()
            owner.get_by_role('button', name='Open E2E shared card', exact=True).click()
            dialog = owner.get_by_role('dialog')
            dialog.get_by_text('Share link', exact=True).click()
            dialog.get_by_role('button', name='Create share link', exact=True).click()
            link = dialog.get_by_role('link', name='Open shared card', exact=True)
            expect(link).to_be_visible()
            share_url = link.get_attribute('href')
            self.assertRegex(share_url, re.escape(self.url) + r'/\?velocity_share=[a-f0-9]{64}$')
            expect(dialog.locator('code')).to_have_text(share_url)
            # Native Streamlit copy control must put the actual URL on clipboard.
            dialog.locator('code').hover()
            dialog.get_by_role('button', name=re.compile('Copy', re.I)).click()
            self.assertEqual(owner.evaluate('navigator.clipboard.readText()'), share_url)

            # Fresh browser context has no creator login/session state.
            viewer.goto(share_url)
            expect(viewer.get_by_role('heading', name='E2E shared card', exact=True)).to_be_visible()
            expect(viewer.locator('th[data-run-id]')).to_have_count(10)
            expect(viewer.get_by_role('cell', name='1,003', exact=True)).to_be_visible()
            expect(viewer.get_by_role('button', name='Refresh results', exact=True)).to_be_visible()
            for label in ['Run', 'Remove card', 'Create share link', 'Revoke share link', 'Save name', 'Write remark']:
                expect(viewer.get_by_role('button', name=label, exact=True)).to_have_count(0)
            for private in ['PRIVATE OTHER CARD', 'PRIVATE RAW COMMENT', 'owner@example.com']:
                expect(viewer.get_by_text(private, exact=False)).to_have_count(0)
            viewer.get_by_role('tab', name='Analysis', exact=True).click()
            expect(viewer.get_by_text('community', exact=True)).to_be_visible()
            expect(viewer.get_by_role('cell', name='Watch this conversation', exact=True).last).to_be_visible()
            viewer.get_by_role('tab', name='Velocity', exact=True).click()

            # A new check through the owner UI becomes visible without a new link.
            dialog.get_by_role('button', name='Run', exact=True).click()
            dialog.get_by_role('button', name='Close notepad', exact=True).click()
            owner.get_by_role('button', name='Refresh cards & checks', exact=True).click()
            expect(owner.get_by_text('1 / 10 posts · 11 saved checks', exact=True)).to_be_visible()
            owner.get_by_role('button', name='Open E2E shared card', exact=True).click()
            expect(dialog).to_be_visible()
            if not dialog.get_by_role('button', name='Revoke share link', exact=True).is_visible():
                dialog.get_by_text('Share link', exact=True).click()
            viewer.get_by_role('button', name='Refresh results', exact=True).click()
            expect(viewer.locator('th[data-run-id]')).to_have_count(11)
            expect(viewer.get_by_role('cell', name='1,103', exact=True)).to_be_visible()

            dialog.get_by_role('button', name='Revoke share link', exact=True).click()
            expect(dialog.get_by_role('button', name='Create share link', exact=True)).to_be_visible()
            viewer.get_by_role('button', name='Refresh results', exact=True).click()
            expect(viewer.get_by_text('This share link is invalid or no longer available.')).to_be_visible()
            expect(viewer.locator('table')).to_have_count(0)

            dialog.get_by_role('button', name='Create share link', exact=True).click()
            expect(link).to_be_visible()
            replacement = link.get_attribute('href')
            self.assertNotEqual(replacement, share_url)
            viewer.goto(share_url)
            expect(viewer.get_by_text('This share link is invalid or no longer available.')).to_be_visible()
            viewer.goto(replacement)
            expect(viewer.get_by_role('heading', name='E2E shared card', exact=True)).to_be_visible()

            dialog.get_by_role('button', name='Remove card', exact=True).click()
            expect(owner.get_by_role('button', name='Open E2E shared card', exact=True)).to_have_count(0)
            viewer.reload()
            expect(viewer.get_by_text('This share link is invalid or no longer available.')).to_be_visible()
            viewer.goto(self.url + '/?velocity_share=invalid')
            expect(viewer.get_by_text('This share link is invalid or no longer available.')).to_be_visible()
            viewer.goto(self.url)
            expect(viewer.get_by_role('heading', name='Sign in to test workspace')).to_be_visible()
            expect(viewer.get_by_role('button', name='Open PRIVATE OTHER CARD')).to_have_count(0)
            for page in [owner, viewer]:
                expect(page.get_by_test_id('stException')).to_have_count(0)
            self.assertEqual(errors, [])
        except Exception:
            artifacts = Path(os.getenv('E2E_ARTIFACT_DIR', '.pytest_cache/velocity-sharing-e2e'))
            artifacts.mkdir(parents=True, exist_ok=True)
            for name, page, context in [('owner', owner, owner_context), ('viewer', viewer, viewer_context)]:
                try:
                    page.screenshot(path=str(artifacts / f'{name}.png'), full_page=True)
                    context.tracing.stop(path=str(artifacts / f'{name}-trace.zip'))
                except Exception:
                    pass  # Keep the original failure if Chromium itself crashed.
            (artifacts / 'streamlit.log').write_text(self.log_path.read_text())
            # All persisted diagnostics contain only synthetic fixture data.
            print('\nStreamlit E2E server log:\n' + self.log_path.read_text()[-12000:])
            raise
