"""Exercise a real native agent using a dashboard-generated device credential."""
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server


def main(executable):
    with tempfile.TemporaryDirectory() as directory:
        admin, enrollment = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        server = create_server(('127.0.0.1', 0), str(Path(directory) / 'db.sqlite'), admin, enrollment)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            req = urllib.request.Request(url + '/api/agents/windows/download', data=b'{}', headers={'Authorization': 'Bearer ' + admin})
            with urllib.request.urlopen(req) as response:
                content = response.read()
            with zipfile.ZipFile(io.BytesIO(content)) as bundle:
                state = Path(directory) / 'device.json'
                state.write_bytes(bundle.read('device.json'))
            # Native agent needs only its scoped device state, never admin/enrollment credentials.
            environment = {k: v for k, v in os.environ.items() if k not in ('RMM_ADMIN_TOKEN', 'RMM_ENROLLMENT_TOKEN', 'RMM_TURN_SECRET')}
            denied = subprocess.run([executable, '--state', str(state), '--once'], capture_output=True, text=True, env=environment, timeout=30)
            assert denied.returncode == 2 and '--consent' in denied.stderr
            subprocess.run([executable, '--state', str(state), '--once', '--consent'], check=True, env=environment, timeout=30)
            req = urllib.request.Request(url + '/api/devices', headers={'Authorization': 'Bearer ' + admin})
            with urllib.request.urlopen(req) as response:
                devices = json.load(response)['devices']
            assert len(devices) == 1 and devices[0]['status'] == 'online'
            assert devices[0]['inventory']['agent_version'] == '0.2.0'
            assert devices[0]['inventory']['metrics']['memory_total_bytes'] > 0
            identifier = json.loads(state.read_text())['id']
            req = urllib.request.Request(url + '/api/devices/' + identifier + '/revoke', data=b'{}', headers={'Authorization': 'Bearer ' + admin})
            with urllib.request.urlopen(req):
                pass
            rejected = subprocess.run([executable, '--state', str(state), '--once', '--consent'], env=environment, capture_output=True, text=True, timeout=30)
            assert rejected.returncode != 0 and 'HTTP 401' in rejected.stderr
            print('Native agent passed: dashboard download, consent, device-scoped credential, real OS health, online status, revocation.')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    main(str(Path(sys.argv[1]).resolve()))
