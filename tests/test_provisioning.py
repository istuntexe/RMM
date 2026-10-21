import io
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
import agent
from server import create_server
from provisioning import ROOT, artifact_info


class AgentProvisioning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.admin, self.enrollment = 'a' * 40, 'e' * 40
        self.server = create_server(('127.0.0.1', 0), str(Path(self.temp.name) / 'database.sqlite'), self.admin, self.enrollment)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, token=None, body=None):
        headers = {'Authorization': 'Bearer ' + (token or self.admin)}
        payload = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=payload, headers=headers)
        return urllib.request.urlopen(request)

    def download(self):
        with self.request('/api/agents/windows/download', body={}) as response:
            self.assertEqual(response.headers['Content-Type'], 'application/zip')
            data = response.read()
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            self.assertEqual(set(bundle.namelist()), {'hybridrmm-agent.exe', 'device.json', 'install.ps1', 'uninstall.ps1', 'README.txt', 'SHA256SUMS.txt', 'THIRD_PARTY_NOTICES.txt'})
            self.assertTrue(bundle.read('hybridrmm-agent.exe').startswith(b'MZ'))
            return json.loads(bundle.read('device.json'))

    def test_download_creates_unique_device_without_global_credentials(self):
        first, second = self.download(), self.download()
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['device_token'], second['device_token'])
        self.assertNotIn(first['device_token'], [self.admin, self.enrollment])
        self.assertEqual(first['server'], self.url)
        with self.request('/api/devices') as response:
            devices = json.load(response)['devices']
        self.assertEqual(len(devices), 2)
        self.assertTrue(all(d['status'] == 'pending' for d in devices))
        self.assertNotIn(first['device_token'], json.dumps(devices))
        with self.request('/api/audit') as response:
            self.assertEqual([e['event'] for e in json.load(response)['events']], ['installer_created', 'installer_created'])

    def test_provisioned_device_reports_metrics_and_revocation_blocks_it(self):
        config = self.download()
        inventory = dict(agent.inventory(), metrics=dict(cpu_percent=23.5, memory_total_bytes=8000, memory_used_bytes=4000, disk_total_bytes=10000, disk_used_bytes=6000, uptime_seconds=3600))
        with self.request('/api/heartbeat', config['device_token'], inventory) as response:
            self.assertEqual(response.status, 200)
        with self.request('/api/devices/' + config['id']) as response:
            data = json.load(response)
        self.assertEqual(data['device']['status'], 'online')
        self.assertEqual(data['history'][0]['metrics'], inventory['metrics'])
        with self.request('/api/devices/' + config['id'] + '/revoke', body={}):
            pass
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/heartbeat', config['device_token'], inventory)
        self.addCleanup(error.exception.close)
        self.assertEqual(error.exception.code, 401)
        with self.request('/api/devices/' + config['id']) as response:
            self.assertEqual(json.load(response)['device']['status'], 'revoked')

    def test_admin_download_and_revocation_require_admin_credential(self):
        for path in ('/api/agents/windows/download', '/api/devices/example/revoke'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, self.enrollment, {})
            self.addCleanup(error.exception.close)
            self.assertEqual(error.exception.code, 401)
        with self.request('/api/devices') as response:
            self.assertEqual(json.load(response)['devices'], [])

    def test_rejects_nonfinite_boolean_and_out_of_range_health(self):
        config = self.download()
        for metrics in ({'cpu_percent': math.nan}, {'cpu_percent': 101}, {'cpu_percent': True}, {'memory_total_bytes': 1, 'memory_used_bytes': 2}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/heartbeat', config['device_token'], dict(agent.inventory(), metrics=metrics))
            self.addCleanup(error.exception.close)
            self.assertEqual(error.exception.code, 400)

    def test_migrates_old_database_without_losing_device(self):
        database = Path(self.temp.name) / 'old.sqlite'
        inventory = json.dumps(agent.inventory())
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TABLE devices (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, inventory TEXT NOT NULL, last_seen REAL NOT NULL)')
            db.execute('INSERT INTO devices VALUES (?,?,?,?)', ('old-device', 'old-hash', inventory, 12345))
        migrated = create_server(('127.0.0.1', 0), str(database), self.admin, self.enrollment)
        migrated.server_close()
        with closing(sqlite3.connect(database)) as db:
            row = db.execute('SELECT id,token_hash,inventory,last_seen,revoked FROM devices').fetchone()
        self.assertEqual(row, ('old-device', 'old-hash', inventory, 12345.0, 0))

    def test_unavailable_artifact_returns_error_without_creating_device(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.server = create_server(('127.0.0.1', 0), str(Path(self.temp.name) / 'db-empty.sqlite'), self.admin, self.enrollment, artifacts_dir=self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.url = f'http://127.0.0.1:{self.server.server_port}'
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/agents/windows/download', body={})
        self.addCleanup(error.exception.close)
        self.assertEqual(error.exception.code, 503)
        with self.request('/api/devices') as response:
            self.assertEqual(json.load(response)['devices'], [])

    def test_artifact_integrity_check_and_unavailable_build(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(artifact_info(directory)['available'])
            Path(directory, 'hybridrmm-agent.exe').write_bytes(b'changed')
            Path(directory, 'SHA256SUMS.txt').write_text('0' * 64 + '  hybridrmm-agent.exe\n')
            self.assertFalse(artifact_info(directory)['available'])
        self.assertTrue(artifact_info(ROOT / 'agent-binaries' / 'windows-amd64')['available'])
