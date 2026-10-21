"""Real Chromium checks against an actual inventory server."""
import json
import os
from contextlib import closing
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import threading
import unittest
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent
from server import create_server


class Dashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        options = {'headless': True}
        if os.environ.get('RMM_CHROMIUM_PATH'):
            options['executable_path'] = os.environ['RMM_CHROMIUM_PATH']
        cls.browser = cls.playwright.chromium.launch(**options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / 'db.sqlite')
        self.admin, self.enrollment = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.server = create_server(('127.0.0.1', 0), self.database, self.admin, self.enrollment)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)

    def tearDown(self):
        self.context.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()
        self.assertEqual(self.errors, [], 'Browser JavaScript errors')

    def refresh(self, token):
        self.page.get_by_label('Admin token').fill(token)
        self.page.get_by_role('button', name='Refresh devices').click()

    def test_wrong_credentials_and_empty_state(self):
        self.refresh('invalid')
        expect(self.page.get_by_role('status')).to_have_text('Authentication failed')
        expect(self.page.locator('#devices tr')).to_have_count(0)
        self.refresh(self.admin)
        expect(self.page.get_by_role('status')).to_have_text('0 enrolled device(s)')

    def test_enrollment_heartbeat_offline_and_session_storage(self):
        enrolled = agent.request(self.url, '/api/enroll', self.enrollment)
        self.refresh(self.admin)
        expect(self.page.get_by_role('status')).to_have_text('1 enrolled device(s)')
        expect(self.page.locator('#devices')).to_contain_text('Online')
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE devices SET last_seen = 0')
        self.refresh(self.admin)
        expect(self.page.locator('#devices')).to_contain_text('Offline')
        agent.request(self.url, '/api/heartbeat', enrolled['device_token'])
        self.refresh(self.admin)
        expect(self.page.locator('#devices')).to_contain_text('Online')
        self.assertEqual(self.page.evaluate('Object.keys(localStorage).length + Object.keys(sessionStorage).length'), 0)
        self.page.reload()
        expect(self.page.get_by_label('Admin token')).to_have_value('')

    def test_inventory_is_rendered_as_text_and_auth_failure_clears_rows(self):
        agent.request(self.url, '/api/enroll', self.enrollment)
        payload = agent.inventory()
        payload['hostname'] = '<img src=x onerror="window.injected=true">'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE devices SET inventory = ?', (json.dumps(payload),))
        self.refresh(self.admin)
        expect(self.page.locator('#devices')).to_contain_text(payload['hostname'])
        expect(self.page.locator('#devices img')).to_have_count(0)
        self.assertFalse(self.page.evaluate('Boolean(window.injected)'))
        self.refresh('invalid')
        expect(self.page.get_by_role('status')).to_have_text('Authentication failed')
        expect(self.page.locator('#devices tr')).to_have_count(0)

    def test_network_failure_and_retry(self):
        self.page.route('**/api/devices', lambda route: route.abort())
        self.refresh(self.admin)
        expect(self.page.get_by_role('status')).not_to_have_text('Enter your admin token to load inventory.')
        expect(self.page.locator('#devices tr')).to_have_count(0)
        self.page.unroute('**/api/devices')
        self.refresh(self.admin)
        expect(self.page.get_by_role('status')).to_have_text('0 enrolled device(s)')

    def test_mobile_layout_and_desktop_screenshot(self):
        agent.request(self.url, '/api/enroll', self.enrollment)
        self.refresh(self.admin)
        expect(self.page.locator('#devices tr')).to_have_count(1)
        Path('build/ui').mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path='build/ui/dashboard-desktop.png', full_page=True)
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.page.screenshot(path='build/ui/dashboard-mobile.png', full_page=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
