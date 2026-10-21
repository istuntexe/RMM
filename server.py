"""Small inventory server. Put behind an HTTPS reverse proxy for remote access."""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_server(address, database, admin_token, enrollment_token):
    if min(len(admin_token), len(enrollment_token)) < 32 or admin_token == enrollment_token:
        raise ValueError('Use distinct admin and enrollment tokens of at least 32 characters')
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, inventory TEXT NOT NULL, last_seen REAL NOT NULL)')
    os.chmod(database, 0o600)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log request credentials or inventory.

        def reply(self, status, body, content_type='application/json'):
            payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def bearer(self):
            header = self.headers.get('Authorization', '')
            return header[7:] if header.startswith('Bearer ') else ''

        def authorized(self, expected):
            return hmac.compare_digest(digest(self.bearer()), digest(expected))

        def do_GET(self):
            if self.path == '/healthz':
                return self.reply(200, {'status': 'ok'})
            if self.path == '/':
                return self.reply(200, Path(__file__).with_name('dashboard.html').read_text(), 'text/html; charset=utf-8')
            if self.path != '/api/devices':
                return self.reply(404, {'error': 'not found'})
            if not self.authorized(admin_token):
                return self.reply(401, {'error': 'unauthorized'})
            with closing(sqlite3.connect(database)) as db, db:
                rows = db.execute('SELECT id, inventory, last_seen FROM devices ORDER BY last_seen DESC').fetchall()
            return self.reply(200, {'devices': [{'id': r[0], 'inventory': json.loads(r[1]), 'last_seen': r[2], 'online': time.time() - r[2] < 180} for r in rows]})

        def do_POST(self):
            if self.path not in ('/api/enroll', '/api/heartbeat'):
                return self.reply(404, {'error': 'not found'})
            enrolling = self.path == '/api/enroll'
            if enrolling and not self.authorized(enrollment_token):
                return self.reply(401, {'error': 'unauthorized'})
            device_id = None
            if not enrolling:
                with closing(sqlite3.connect(database)) as db, db:
                    row = db.execute('SELECT id FROM devices WHERE token_hash = ?', (digest(self.bearer()),)).fetchone()
                if not row:
                    return self.reply(401, {'error': 'unauthorized'})
                device_id = row[0]
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 < length <= 16384:
                    return self.reply(413, {'error': 'body must be between 1 and 16384 bytes'})
                inventory = json.loads(self.rfile.read(length))
                fields = {'hostname', 'os', 'release', 'architecture', 'agent_version'}
                if not isinstance(inventory, dict) or set(inventory) != fields or any(not isinstance(v, str) or len(v) > 256 for v in inventory.values()):
                    raise ValueError()
            except (ValueError, UnicodeError):
                return self.reply(400, {'error': 'invalid inventory'})
            with closing(sqlite3.connect(database)) as db, db:
                if enrolling:
                    device_id, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
                    db.execute('INSERT INTO devices VALUES (?, ?, ?, ?)', (device_id, digest(token), json.dumps(inventory), time.time()))
                else:
                    db.execute('UPDATE devices SET inventory = ?, last_seen = ? WHERE id = ?', (json.dumps(inventory), time.time(), device_id))
            return self.reply(201 if enrolling else 200, {'id': device_id, 'device_token': token} if enrolling else {'status': 'ok'})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(10)
            return connection, address

    return Server(address, Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--database', default='data/rmm.sqlite3')
    args = parser.parse_args()
    server = create_server((args.host, args.port), args.database, os.environ.get('RMM_ADMIN_TOKEN', ''), os.environ.get('RMM_ENROLLMENT_TOKEN', ''))
    print(f'Inventory server listening on {args.host}:{server.server_port}', flush=True)
    server.serve_forever()
