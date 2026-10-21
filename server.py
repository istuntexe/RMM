"""Hybrid RMM inventory and agent provisioning pilot. Run behind HTTPS."""
import argparse
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from provisioning import artifact_info, make_bundle

ROOT = Path(__file__).resolve().parent
METRICS = {'cpu_percent', 'memory_total_bytes', 'memory_used_bytes', 'disk_total_bytes', 'disk_used_bytes', 'uptime_seconds'}


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def validate_inventory(inventory):
    fields = {'hostname', 'os', 'release', 'architecture', 'agent_version'}
    if not isinstance(inventory, dict) or set(inventory) - {'metrics'} != fields:
        return False
    if any(not isinstance(inventory[k], str) or len(inventory[k]) > 256 for k in fields):
        return False
    metrics = inventory.get('metrics', {})
    if not isinstance(metrics, dict) or set(metrics) - METRICS:
        return False
    for key, value in metrics.items():
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= (100 if key == 'cpu_percent' else 1e18):
            return False
    for kind in ('memory', 'disk'):
        if metrics.get(kind + '_used_bytes', 0) > metrics.get(kind + '_total_bytes', 1e18):
            return False
    return True


def create_server(address, database, admin_token, enrollment_token, support_ws_url=None, public_url=None, artifacts_dir=None):
    if min(len(admin_token), len(enrollment_token)) < 32 or admin_token == enrollment_token:
        raise ValueError('Use distinct admin and enrollment tokens of at least 32 characters')
    websocket_origin = ''
    if support_ws_url:
        parsed = urlsplit(support_ws_url)
        if parsed.scheme not in ('ws', 'wss') or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() or c in ";'\"" for c in support_ws_url):
            raise ValueError('Invalid signaling URL')
        websocket_origin = f'{parsed.scheme}://{parsed.netloc}'
    if public_url:
        parsed = urlsplit(public_url)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/') or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1'))):
            raise ValueError('Public URL must be HTTPS except for loopback')
    artifacts_dir = Path(artifacts_dir or ROOT / 'agent-binaries' / 'windows-amd64')
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, inventory TEXT NOT NULL, last_seen REAL NOT NULL)')
        if 'revoked' not in {row[1] for row in db.execute('PRAGMA table_info(devices)')}:
            db.execute('ALTER TABLE devices ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE TABLE IF NOT EXISTS admin_audit (id INTEGER PRIMARY KEY, device_id TEXT, event TEXT NOT NULL, occurred_at REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS health_samples (id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, metrics TEXT NOT NULL, occurred_at REAL NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS health_device_time ON health_samples(device_id, occurred_at)')
    os.chmod(database, 0o600)

    def audit(db, device_id, event):
        db.execute('INSERT INTO admin_audit(device_id,event,occurred_at) VALUES (?,?,?)', (device_id, event, time.time()))

    def device(row):
        status = 'revoked' if row[4] else 'pending' if row[3] == 0 else 'online' if time.time() - row[3] < 180 else 'offline'
        return {'id': row[0], 'inventory': json.loads(row[2]), 'last_seen': row[3], 'online': status == 'online', 'status': status, 'revoked': bool(row[4])}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body, content_type='application/json', headers=None):
            payload = body if isinstance(body, bytes) else body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' {websocket_origin}; media-src 'self' blob:; frame-ancestors 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def bearer(self):
            header = self.headers.get('Authorization', '')
            return header[7:] if header.startswith('Bearer ') else ''

        def authorized(self, expected):
            return hmac.compare_digest(digest(self.bearer()), digest(expected))

        def read_json(self):
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 < length <= 16384:
                    self.reply(413, {'error': 'body must be between 1 and 16384 bytes'})
                    return None
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError()
                return value
            except (ValueError, UnicodeError):
                self.reply(400, {'error': 'invalid JSON object'})
                return None

        def do_GET(self):
            if self.path == '/healthz':
                return self.reply(200, {'status': 'ok'})
            pages = {'/': ('dashboard.html', 'text/html; charset=utf-8'), '/dashboard.js': ('dashboard.js', 'text/javascript; charset=utf-8'),
                     '/share': ('support.html', 'text/html; charset=utf-8'), '/viewer': ('support.html', 'text/html; charset=utf-8'), '/support.js': ('support.js', 'text/javascript; charset=utf-8')}
            if self.path in pages:
                filename, content_type = pages[self.path]
                return self.reply(200, (ROOT / filename).read_text(), content_type)
            if self.path == '/api/support/config':
                return self.reply(200, {'websocket_url': support_ws_url})
            if not self.authorized(admin_token):
                return self.reply(401, {'error': 'unauthorized'})
            if self.path == '/api/agents':
                return self.reply(200, artifact_info(artifacts_dir))
            if self.path == '/api/devices':
                with closing(sqlite3.connect(database)) as db:
                    rows = db.execute('SELECT id,token_hash,inventory,last_seen,revoked FROM devices ORDER BY last_seen DESC').fetchall()
                return self.reply(200, {'devices': [device(r) for r in rows]})
            if self.path == '/api/audit':
                with closing(sqlite3.connect(database)) as db:
                    rows = db.execute('SELECT device_id,event,occurred_at FROM admin_audit ORDER BY id DESC LIMIT 100').fetchall()
                return self.reply(200, {'events': [{'device_id': r[0], 'event': r[1], 'occurred_at': r[2]} for r in rows]})
            parts = self.path.split('/')
            if len(parts) == 4 and parts[1:3] == ['api', 'devices']:
                with closing(sqlite3.connect(database)) as db:
                    row = db.execute('SELECT id,token_hash,inventory,last_seen,revoked FROM devices WHERE id=?', (parts[3],)).fetchone()
                    samples = db.execute('SELECT metrics,occurred_at FROM health_samples WHERE device_id=? ORDER BY id DESC LIMIT 60', (parts[3],)).fetchall() if row else []
                if row:
                    return self.reply(200, {'device': device(row), 'history': [{'metrics': json.loads(s[0]), 'occurred_at': s[1]} for s in samples]})
            return self.reply(404, {'error': 'not found'})

        def do_POST(self):
            if self.path == '/api/agents/windows/download':
                if not self.authorized(admin_token):
                    return self.reply(401, {'error': 'unauthorized'})
                body = self.read_json()
                if body is None:
                    return
                info = artifact_info(artifacts_dir)
                if not info['available']:
                    return self.reply(503, {'error': info['reason']})
                identifier, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
                config = {'server': httpd.public_url, 'id': identifier, 'device_token': token}
                bundle = make_bundle(artifacts_dir, config)
                inventory = dict(hostname='Awaiting installation', os='Windows', release='', architecture='amd64', agent_version='0.2.0')
                with closing(sqlite3.connect(database)) as db, db:
                    db.execute('INSERT INTO devices(id,token_hash,inventory,last_seen) VALUES(?,?,?,0)', (identifier, digest(token), json.dumps(inventory)))
                    audit(db, identifier, 'installer_created')
                return self.reply(200, bundle, 'application/zip', {'Content-Disposition': f'attachment; filename="hybridrmm-agent-{identifier}.zip"'})
            parts = self.path.split('/')
            if len(parts) == 5 and parts[1:3] == ['api', 'devices'] and parts[4] == 'revoke':
                if not self.authorized(admin_token):
                    return self.reply(401, {'error': 'unauthorized'})
                if self.read_json() is None:
                    return
                with closing(sqlite3.connect(database)) as db, db:
                    row = db.execute('SELECT revoked FROM devices WHERE id=?', (parts[3],)).fetchone()
                    if not row:
                        return self.reply(404, {'error': 'not found'})
                    if not row[0]:
                        db.execute('UPDATE devices SET revoked=1 WHERE id=?', (parts[3],))
                        audit(db, parts[3], 'credential_revoked')
                return self.reply(200, {'status': 'revoked'})
            if self.path not in ('/api/enroll', '/api/heartbeat'):
                return self.reply(404, {'error': 'not found'})
            enrolling = self.path == '/api/enroll'
            if enrolling and not self.authorized(enrollment_token):
                return self.reply(401, {'error': 'unauthorized'})
            identifier, first_report = None, False
            if not enrolling:
                with closing(sqlite3.connect(database)) as db:
                    row = db.execute('SELECT id,last_seen FROM devices WHERE token_hash=? AND revoked=0', (digest(self.bearer()),)).fetchone()
                if not row:
                    return self.reply(401, {'error': 'unauthorized'})
                identifier, first_report = row[0], row[1] == 0
            inventory = self.read_json()
            if inventory is None:
                return
            if not validate_inventory(inventory):
                return self.reply(400, {'error': 'invalid inventory'})
            with closing(sqlite3.connect(database)) as db, db:
                if enrolling:
                    identifier, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
                    db.execute('INSERT INTO devices(id,token_hash,inventory,last_seen) VALUES(?,?,?,?)', (identifier, digest(token), json.dumps(inventory), time.time()))
                    audit(db, identifier, 'device_enrolled')
                else:
                    changed = db.execute('UPDATE devices SET inventory=?,last_seen=? WHERE id=? AND revoked=0', (json.dumps(inventory), time.time(), identifier)).rowcount
                    if not changed:
                        return self.reply(401, {'error': 'unauthorized'})
                    if first_report:
                        audit(db, identifier, 'device_connected')
                if inventory.get('metrics'):
                    db.execute('INSERT INTO health_samples(device_id,metrics,occurred_at) VALUES(?,?,?)', (identifier, json.dumps(inventory['metrics']), time.time()))
                    db.execute('DELETE FROM health_samples WHERE device_id=? AND id NOT IN (SELECT id FROM health_samples WHERE device_id=? ORDER BY id DESC LIMIT 1440)', (identifier, identifier))
            return self.reply(201 if enrolling else 200, {'id': identifier, 'device_token': token} if enrolling else {'status': 'ok'})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(10)
            return connection, address

    httpd = Server(address, Handler)
    httpd.public_url = (public_url or f'http://127.0.0.1:{httpd.server_port}').rstrip('/')
    return httpd


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--database', default='data/rmm.sqlite3')
    args = parser.parse_args()
    httpd = create_server((args.host, args.port), args.database, os.environ.get('RMM_ADMIN_TOKEN', ''), os.environ.get('RMM_ENROLLMENT_TOKEN', ''), os.environ.get('RMM_SIGNAL_URL'), os.environ.get('RMM_PUBLIC_URL', 'https://hybridrmm.online'), os.environ.get('RMM_AGENT_ARTIFACTS'))
    print(f'Hybrid RMM server listening on {args.host}:{httpd.server_port}', flush=True)
    httpd.serve_forever()
