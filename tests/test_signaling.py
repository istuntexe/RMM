import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus
import agent
from server import create_server
from signaling import Signaling


class SupportSignaling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / 'db.sqlite')
        self.admin, self.enroll = 'a' * 40, 'e' * 40
        http = create_server(('127.0.0.1', 0), self.database, self.admin, self.enroll)
        http.server_close()
        import uuid
        from server import digest
        self.device_id, self.token = str(uuid.uuid4()), 'd' * 40
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO devices VALUES (?, ?, ?, ?)', (self.device_id, digest(self.token), json.dumps(agent.inventory()), 0))
        self.signal = Signaling(self.database, self.admin, ['http://localhost'], turn_secret='s' * 40,
                                turn_urls=('turn:relay.example:3478',), request_timeout=5, session_timeout=5)
        self.server = await self.signal.start(port=0)
        self.url = f'ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/ws'
        self.connections = []

    async def asyncTearDown(self):
        for connection in self.connections:
            await connection.close()
        self.server.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def receive(self, connection):
        return json.loads(await asyncio.wait_for(connection.recv(), timeout=2))

    async def join(self, role, token):
        connection = await connect(self.url, origin='http://localhost')
        self.connections.append(connection)
        await connection.send(json.dumps(dict(type='auth', role=role, device_id=self.device_id, token=token)))
        return connection, await self.receive(connection)

    async def pair(self):
        device, ready = await self.join('agent', self.token)
        self.assertEqual(ready['type'], 'ready')
        viewer, waiting = await self.join('viewer', self.admin)
        self.assertEqual(waiting['type'], 'waiting')
        request = await self.receive(device)
        self.assertEqual(request['type'], 'request')
        self.assertEqual(request['session_id'], waiting['session_id'])
        return device, viewer, request['session_id']

    async def send(self, connection, kind, session_id, **fields):
        await connection.send(json.dumps(dict(type=kind, session_id=session_id, **fields)))

    async def test_role_credentials_are_separate(self):
        for role, token in [('agent', self.admin), ('viewer', self.token), ('viewer', self.enroll), ('wrong', self.admin)]:
            _, result = await self.join(role, token)
            self.assertEqual(result, {'type': 'error', 'error': 'unauthorized'})
        _, result = await self.join('viewer', self.admin)
        self.assertEqual(result['error'], 'device_offline_or_busy')

    async def test_consent_gates_signaling_and_turn_credentials(self):
        device, viewer, session_id = await self.pair()
        await self.send(viewer, 'offer', session_id, sdp='before consent')
        self.assertEqual((await self.receive(viewer))['error'], 'consent_required')
        await self.send(device, 'accept', session_id)
        accepted = await self.receive(device)
        self.assertEqual(accepted, await self.receive(viewer))
        self.assertEqual(accepted['type'], 'accepted')
        turn = accepted['ice_servers'][0]
        expected = base64.b64encode(hmac.new(('s' * 40).encode(), turn['username'].encode(), hashlib.sha1).digest()).decode()
        self.assertEqual(turn['credential'], expected)
        self.assertNotIn('s' * 40, json.dumps(accepted))
        await self.send(viewer, 'offer', session_id, sdp='test offer')
        self.assertEqual((await self.receive(device))['sdp'], 'test offer')
        await self.send(device, 'answer', session_id, sdp='test answer')
        self.assertEqual((await self.receive(viewer))['sdp'], 'test answer')
        await self.send(viewer, 'candidate', session_id, candidate={'candidate': 'candidate:test', 'sdpMid': '0'})
        self.assertEqual((await self.receive(device))['type'], 'candidate')
        await self.send(viewer, 'end', session_id)
        self.assertEqual((await self.receive(device))['reason'], 'stopped')
        self.assertEqual((await self.receive(viewer))['reason'], 'stopped')
        with closing(sqlite3.connect(self.database)) as db:
            events = [r[0] for r in db.execute('SELECT event FROM support_audit ORDER BY id')]
        self.assertEqual(events, ['requested', 'accepted', 'stopped'])

    async def test_reject_request_and_request_again(self):
        device, viewer, session_id = await self.pair()
        await self.send(device, 'reject', session_id)
        self.assertEqual((await self.receive(viewer))['reason'], 'rejected')
        self.assertEqual((await self.receive(device))['reason'], 'rejected')
        _, result = await self.join('viewer', self.admin)
        self.assertEqual(result['type'], 'waiting')
        self.assertNotEqual(result['session_id'], session_id)

    async def test_origin_duplicate_and_session_isolation(self):
        with self.assertRaises(InvalidStatus):
            await connect(self.url, origin='https://untrusted.example')
        device, viewer, session_id = await self.pair()
        _, result = await self.join('agent', self.token)
        self.assertEqual(result['error'], 'device_already_connected')
        _, result = await self.join('viewer', self.admin)
        self.assertEqual(result['error'], 'device_offline_or_busy')
        await self.send(viewer, 'offer', 'another-session', sdp='cross session')
        self.assertEqual((await self.receive(viewer))['error'], 'invalid_session')
        await self.send(viewer, 'accept', session_id)
        self.assertEqual((await self.receive(viewer))['error'], 'consent_required')
        await self.send(device, 'accept', session_id)
        self.assertEqual((await self.receive(device))['type'], 'accepted')
        self.assertEqual((await self.receive(viewer))['type'], 'accepted')
        await self.send(viewer, 'command', session_id, script='not supported')
        self.assertEqual((await self.receive(viewer))['error'], 'unsupported_message')

    async def test_request_and_active_session_expire(self):
        self.signal.request_timeout = .1
        device, viewer, _ = await self.pair()
        self.assertEqual((await self.receive(viewer))['reason'], 'consent_timeout')
        self.assertEqual((await self.receive(device))['reason'], 'consent_timeout')
        self.signal.request_timeout = 5
        self.signal.session_timeout = .1
        viewer, result = await self.join('viewer', self.admin)
        request = await self.receive(device)
        await self.send(device, 'accept', request['session_id'])
        await self.receive(device)
        await self.receive(viewer)
        self.assertEqual((await self.receive(viewer))['reason'], 'session_timeout')
        self.assertEqual((await self.receive(device))['reason'], 'session_timeout')

    async def test_agent_disconnect_ends_session(self):
        device, viewer, _ = await self.pair()
        await device.close()
        self.assertEqual((await self.receive(viewer))['reason'], 'disconnected')
        self.assertEqual(self.signal.agents, {})
        self.assertEqual(self.signal.sessions, {})
