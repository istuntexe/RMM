"""Authenticated, consent-gated WebRTC signaling. Does not execute commands."""
import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from server import digest


@dataclass
class Session:
    device_id: str
    id: str
    agent: object
    viewer: object
    active: bool = False
    timer: object = None


class Signaling:
    def __init__(self, database, admin_token, origins, turn_secret='', turn_urls=(), request_timeout=30, session_timeout=600):
        if len(admin_token) < 32 or not origins:
            raise ValueError('Admin token and explicit browser origins required')
        if bool(turn_secret) != bool(turn_urls):
            raise ValueError('Configure TURN secret and URLs together')
        if any(not u.startswith(('turn:', 'turns:')) for u in turn_urls):
            raise ValueError('TURN URLs must use turn: or turns:')
        self.database, self.admin_token, self.origins = database, admin_token, origins
        self.turn_secret, self.turn_urls = turn_secret, turn_urls
        self.request_timeout, self.session_timeout = request_timeout, session_timeout
        self.agents, self.sessions = {}, {}
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS support_audit (id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, session_id TEXT NOT NULL, event TEXT NOT NULL, occurred_at REAL NOT NULL)')

    def audit(self, session, event):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO support_audit (device_id, session_id, event, occurred_at) VALUES (?, ?, ?, ?)',
                       (session.device_id, session.id, event, time.time()))

    def ice_servers(self, session):
        if not self.turn_urls:
            return []
        username = f'{int(time.time()) + int(self.session_timeout) + 60}:{session.id}'
        password = base64.b64encode(hmac.new(self.turn_secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        return [{'urls': list(self.turn_urls), 'username': username, 'credential': password}]

    async def send(self, connection, message):
        try:
            await connection.send(json.dumps(message))
        except ConnectionClosed:
            pass

    async def finish(self, session, reason):
        if self.sessions.get(session.device_id) is not session:
            return
        del self.sessions[session.device_id]
        if session.timer and session.timer is not asyncio.current_task():
            session.timer.cancel()
        self.audit(session, reason)
        await self.send(session.agent, {'type': 'ended', 'session_id': session.id, 'reason': reason})
        await self.send(session.viewer, {'type': 'ended', 'session_id': session.id, 'reason': reason})
        await session.viewer.close()

    async def expire(self, session, seconds, reason):
        await asyncio.sleep(seconds)
        await self.finish(session, reason)

    def authenticated(self, data):
        if set(data) != {'type', 'role', 'device_id', 'token'} or data['type'] != 'auth':
            return False
        if data['role'] not in ('agent', 'viewer') or not isinstance(data['token'], str) or len(data['token']) > 512:
            return False
        try:
            if str(uuid.UUID(data['device_id'])) != data['device_id']:
                return False
        except (ValueError, TypeError, AttributeError):
            return False
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute('SELECT token_hash FROM devices WHERE id = ? AND revoked = 0', (data['device_id'],)).fetchone()
        if not row:
            return False
        expected = digest(self.admin_token) if data['role'] == 'viewer' else row[0]
        return hmac.compare_digest(digest(data['token']), expected)

    async def handler(self, connection):
        role, device_id = None, None
        try:
            if connection.request.path != '/ws':
                await connection.close(1008, 'Unknown endpoint')
                return
            data = json.loads(await asyncio.wait_for(connection.recv(), timeout=5))
            if not isinstance(data, dict) or not self.authenticated(data):
                await self.send(connection, {'type': 'error', 'error': 'unauthorized'})
                await connection.close(1008, 'Unauthorized')
                return
            role, device_id = data['role'], data['device_id']
            if role == 'agent':
                if device_id in self.agents:
                    await self.send(connection, {'type': 'error', 'error': 'device_already_connected'})
                    return
                self.agents[device_id] = connection
                await self.send(connection, {'type': 'ready', 'role': role})
            else:
                if device_id not in self.agents or device_id in self.sessions:
                    await self.send(connection, {'type': 'error', 'error': 'device_offline_or_busy'})
                    return
                session = Session(device_id, str(uuid.uuid4()), self.agents[device_id], connection)
                self.sessions[device_id] = session
                self.audit(session, 'requested')
                session.timer = asyncio.create_task(self.expire(session, self.request_timeout, 'consent_timeout'))
                await self.send(connection, {'type': 'waiting', 'session_id': session.id})
                await self.send(session.agent, {'type': 'request', 'session_id': session.id, 'expires_in': self.request_timeout})
            window, count = time.monotonic(), 0
            async for raw in connection:
                if time.monotonic() - window > 1:
                    window, count = time.monotonic(), 0
                count += 1
                if count > 60:
                    await connection.close(1008, 'Rate limit')
                    break
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Invalid message')
                session = self.sessions.get(device_id)
                if not session or message.get('session_id') != session.id:
                    await self.send(connection, {'type': 'error', 'error': 'invalid_session'})
                    continue
                kind = message.get('type')
                if kind == 'end' or (kind == 'reject' and role == 'agent' and not session.active):
                    await self.finish(session, 'rejected' if kind == 'reject' else 'stopped')
                    continue
                if kind == 'accept' and role == 'agent' and not session.active:
                    session.active = True
                    session.timer.cancel()
                    session.timer = asyncio.create_task(self.expire(session, self.session_timeout, 'session_timeout'))
                    self.audit(session, 'accepted')
                    accepted = {'type': 'accepted', 'session_id': session.id, 'ice_servers': self.ice_servers(session), 'expires_in': self.session_timeout}
                    await self.send(session.agent, accepted)
                    await self.send(session.viewer, accepted)
                    continue
                if not session.active:
                    await self.send(connection, {'type': 'error', 'error': 'consent_required'})
                    continue
                if kind in ('offer', 'answer') and role == ('viewer' if kind == 'offer' else 'agent'):
                    if not isinstance(message.get('sdp'), str) or not 1 <= len(message['sdp']) <= 60000:
                        raise ValueError('Invalid SDP')
                    outgoing = {'type': kind, 'sdp': message['sdp'], 'session_id': session.id}
                elif kind == 'candidate':
                    candidate = message.get('candidate')
                    if candidate is not None and (not isinstance(candidate, dict) or len(json.dumps(candidate)) > 4096 or set(candidate) - {'candidate', 'sdpMid', 'sdpMLineIndex', 'usernameFragment'}):
                        raise ValueError('Invalid candidate')
                    outgoing = {'type': kind, 'candidate': candidate, 'session_id': session.id}
                else:
                    await self.send(connection, {'type': 'error', 'error': 'unsupported_message'})
                    continue
                await self.send(session.viewer if role == 'agent' else session.agent, outgoing)
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        except (ValueError, TypeError):
            await connection.close(1008, 'Invalid message')
        finally:
            if role == 'agent' and self.agents.get(device_id) is connection:
                del self.agents[device_id]
            session = self.sessions.get(device_id)
            if session and connection in (session.agent, session.viewer):
                await self.finish(session, 'disconnected')

    async def start(self, host='127.0.0.1', port=8081):
        return await serve(self.handler, host, port, origins=self.origins,
                           max_size=65536, max_queue=16, open_timeout=10, close_timeout=2, ping_interval=20)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default='data/rmm.sqlite3')
    parser.add_argument('--port', type=int, default=8081)
    args = parser.parse_args()
    signal = Signaling(args.database, os.environ.get('RMM_ADMIN_TOKEN', ''),
                       os.environ.get('RMM_ALLOWED_ORIGINS', 'https://hybridrmm.online').split(','),
                       os.environ.get('RMM_TURN_SECRET', ''),
                       tuple(filter(None, os.environ.get('RMM_TURN_URLS', '').split(','))))
    async with await signal.start(port=args.port):
        print('Consent-gated signaling listening on loopback', flush=True)
        await asyncio.Future()


if __name__ == '__main__':
    asyncio.run(main())
