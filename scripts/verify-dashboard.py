"""Real Chromium tests of admin sign-in, agent download and monitoring."""
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from contextlib import closing
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
        cls.browser.close(); cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / 'db.sqlite')
        self.admin, self.enrollment = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.server = create_server(('127.0.0.1', 0), self.database, self.admin, self.enrollment)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.context = self.browser.new_context(accept_downloads=True)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)

    def tearDown(self):
        self.context.close(); self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()
        self.assertEqual(self.errors, [], 'Browser JavaScript errors')

    def login(self, token=None):
        self.page.get_by_label('Admin token', exact=True).fill(token or self.admin)
        self.page.get_by_role('button', name='Sign in', exact=True).click()
        if token is None:
            expect(self.page.locator('#app')).to_be_visible()

    def report(self, token, hostname='LAB-WINDOWS-01'):
        inventory = dict(agent.inventory(), hostname=hostname, os='Windows', release='10.0 build 26100', metrics=dict(cpu_percent=23.5, memory_total_bytes=8589934592, memory_used_bytes=4294967296, disk_total_bytes=107374182400, disk_used_bytes=64424509440, uptime_seconds=7200))
        request = urllib.request.Request(self.url + '/api/heartbeat', data=json.dumps(inventory).encode(), headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request):
            pass

    def test_signin_empty_state_and_memory_only_credentials(self):
        self.login('wrong')
        expect(self.page.get_by_role('status')).to_contain_text('Authentication failed')
        expect(self.page.locator('#app')).to_be_hidden()
        self.login()
        expect(self.page.locator('#total')).to_have_text('0')
        expect(self.page.locator('#empty')).to_contain_text('Download an agent installer')
        expect(self.page.locator('#token')).to_have_value('')
        self.assertEqual(self.page.evaluate('Object.keys(localStorage).length + Object.keys(sessionStorage).length'), 0)
        self.page.reload()
        expect(self.page.locator('#login-screen')).to_be_visible()

    def test_agent_download_pending_to_online_health_details_and_revocation(self):
        self.login()
        with self.page.expect_download() as download_info:
            self.page.locator('#download-top').click()
        download = download_info.value
        self.assertRegex(download.suggested_filename, r'^hybridrmm-agent-.*\.zip$')
        saved = Path(self.temp.name) / 'bundle.zip'; download.save_as(saved)
        with zipfile.ZipFile(saved) as bundle:
            config = json.loads(bundle.read('device.json'))
            self.assertTrue(bundle.read('hybridrmm-agent.exe').startswith(b'MZ'))
        expect(self.page.locator('#pending')).to_have_text('1')
        self.report(config['device_token']); self.report(config['device_token'])
        self.page.locator('#refresh').click()
        expect(self.page.locator('#online')).to_have_text('1')
        expect(self.page.locator('#devices')).to_contain_text('LAB-WINDOWS-01')
        expect(self.page.locator('#devices')).to_contain_text('23.5%')
        expect(self.page.locator('#devices')).to_contain_text('50.0%')
        self.page.get_by_role('button', name='Details', exact=True).click()
        expect(self.page.locator('#device-dialog')).to_be_visible()
        expect(self.page.locator('#details')).to_contain_text('2 hours')
        expect(self.page.locator('#history svg')).to_have_count(1)
        self.page.get_by_role('button', name='Close', exact=True).click()
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.get_by_role('button', name='Revoke', exact=True).click()
        expect(self.page.locator('#devices .badge')).to_have_text('Revoked')
        expect(self.page.locator('#online')).to_have_text('0')
        self.page.get_by_role('button', name='Activity log', exact=True).click()
        expect(self.page.locator('#audit')).to_contain_text('credential revoked')
        expect(self.page.locator('#audit')).to_contain_text('installer created')

    def test_online_offline_search_filter_and_signout(self):
        enrolled = agent.request(self.url, '/api/enroll', self.enrollment)
        self.report(enrolled['device_token'])
        self.login()
        expect(self.page.locator('#online')).to_have_text('1')
        self.page.get_by_label('Search devices').fill('does-not-exist')
        expect(self.page.locator('#devices tr')).to_have_count(0)
        self.page.get_by_label('Search devices').fill('LAB-WINDOWS')
        expect(self.page.locator('#devices tr')).to_have_count(1)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE devices SET last_seen=?', (time.time() - 200,))
        self.page.locator('#refresh').click()
        expect(self.page.locator('#offline')).to_have_text('1')
        self.page.get_by_label('Filter devices').select_option('online')
        expect(self.page.locator('#devices tr')).to_have_count(0)
        self.page.get_by_label('Filter devices').select_option('offline')
        expect(self.page.locator('#devices tr')).to_have_count(1)
        self.page.locator('#logout').click()
        expect(self.page.locator('#app')).to_be_hidden()
        expect(self.page.locator('#devices tr')).to_have_count(0)

    def test_inventory_is_rendered_as_text(self):
        enrolled = agent.request(self.url, '/api/enroll', self.enrollment)
        payload = '<img src=x onerror="window.injected=true">'
        self.report(enrolled['device_token'], payload)
        self.login()
        expect(self.page.locator('#devices')).to_contain_text(payload)
        expect(self.page.locator('#devices img')).to_have_count(0)
        self.assertFalse(self.page.evaluate('Boolean(window.injected)'))

    def test_network_failure_and_retry(self):
        self.login()
        self.page.route('**/api/devices', lambda route: route.abort())
        self.page.locator('#refresh').click()
        expect(self.page.get_by_role('status')).to_contain_text('Failed to fetch')
        self.page.unroute('**/api/devices')
        self.page.locator('#refresh').click()
        expect(self.page.get_by_role('status')).to_have_text('Device inventory refreshed.')

    def test_desktop_mobile_and_agent_page(self):
        enrolled = agent.request(self.url, '/api/enroll', self.enrollment)
        self.report(enrolled['device_token'])
        self.login()
        Path('build/ui').mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path='build/ui/dashboard-desktop.png', full_page=True)
        self.page.get_by_role('button', name='Agent installers', exact=True).click()
        expect(self.page.locator('#build-status')).to_contain_text('Native agent v0.2.0')
        self.page.screenshot(path='build/ui/dashboard-agents.png', full_page=True)
        self.page.get_by_role('button', name='Devices', exact=True).click()
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.page.screenshot(path='build/ui/dashboard-mobile.png', full_page=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
