import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
import agent
import server


class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.admin, self.enroll = 'a' * 40, 'e' * 40
        self.server = server.create_server(('127.0.0.1', 0), str(Path(self.temp.name) / 'db.sqlite'), self.admin, self.enroll)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def devices(self, token):
        req = urllib.request.Request(self.url + '/api/devices', headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req) as response:
            return json.load(response)['devices']

    def test_enrollment_heartbeat_and_dashboard(self):
        state = Path(self.temp.name) / 'state.json'
        with patch.dict(os.environ, {'RMM_ENROLLMENT_TOKEN': self.enroll}):
            agent.run_once(self.url, state)
        devices = self.devices(self.admin)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]['inventory'], agent.inventory())
        first_seen = devices[0]['last_seen']
        agent.run_once(self.url, state)
        devices = self.devices(self.admin)
        self.assertEqual(len(devices), 1)
        self.assertGreater(devices[0]['last_seen'], first_seen)
        self.assertTrue(devices[0]['online'])
        self.assertNotIn('device_token', devices[0])
        with urllib.request.urlopen(self.url) as response:
            self.assertIn(b'Device inventory', response.read())
        with self.assertRaises(ValueError):
            agent.run_once('http://localhost:1234', state)

    def test_authentication_and_input_validation(self):
        for token in ('', self.enroll, 'wrong'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.devices(token)
            self.addCleanup(error.exception.close)
            self.assertEqual(error.exception.code, 401)
        for endpoint in ('/api/enroll', '/api/heartbeat'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                agent.request(self.url, endpoint, self.admin)
            self.addCleanup(error.exception.close)
            self.assertEqual(error.exception.code, 401)
        req = urllib.request.Request(self.url + '/api/enroll', data=b'{"unexpected":"field"}', headers={'Authorization': 'Bearer ' + self.enroll})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.addCleanup(error.exception.close)
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.devices(self.admin), [])

    def test_database_connections_are_closed(self):
        connections = []
        original_connect = sqlite3.connect

        class TrackedConnection(sqlite3.Connection):
            closed = False

            def close(self):
                super().close()
                self.closed = True

        def connect(*args, **kwargs):
            connection = original_connect(*args, factory=TrackedConnection, **kwargs)
            connections.append(connection)
            return connection

        with patch.object(server.sqlite3, 'connect', side_effect=connect):
            extra = server.create_server(('127.0.0.1', 0), str(Path(self.temp.name) / 'extra.sqlite'), self.admin, self.enroll)
            extra.server_close()
            enrolled = agent.request(self.url, '/api/enroll', self.enroll)
            agent.request(self.url, '/api/heartbeat', enrolled['device_token'])
            self.devices(self.admin)
        try:
            self.assertEqual(len(connections), 5)
            self.assertTrue(all(connection.closed for connection in connections), 'Database connections must close before cleanup, including on Windows')
        finally:
            for connection in connections:
                if not connection.closed:
                    connection.close()

    def test_remote_plaintext_rejected(self):
        with self.assertRaises(ValueError):
            agent.request('http://example.com', '/api/enroll', self.enroll)


if __name__ == '__main__':
    unittest.main()
