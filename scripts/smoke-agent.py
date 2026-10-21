"""Exercise an actual agent process, including frozen Windows builds."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server


def main(command):
    if not command:
        raise ValueError('Usage: smoke-agent.py EXECUTABLE [SCRIPT]')
    with tempfile.TemporaryDirectory() as directory:
        admin, enrollment = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        server = create_server(('127.0.0.1', 0), str(Path(directory) / 'db.sqlite'), admin, enrollment)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        arguments = ['--server', base, '--once', '--state', str(Path(directory) / 'device.json')]
        environment = os.environ.copy()
        environment['RMM_ENROLLMENT_TOKEN'] = enrollment
        try:
            denied = subprocess.run(command + arguments, env=environment, capture_output=True, text=True, timeout=60)
            assert denied.returncode == 2 and '--consent' in denied.stderr, 'Agent must require consent'
            subprocess.run(command + arguments + ['--consent'], env=environment, check=True, timeout=60)
            # Subsequent runs must use saved device credentials, not enrollment credentials.
            environment.pop('RMM_ENROLLMENT_TOKEN')
            subprocess.run(command + arguments + ['--consent'], env=environment, check=True, timeout=60)
            req = urllib.request.Request(base + '/api/devices', headers={'Authorization': 'Bearer ' + admin})
            with urllib.request.urlopen(req, timeout=10) as response:
                devices = json.load(response)['devices']
            assert len(devices) == 1 and devices[0]['online'], 'Expected one online device'
            assert devices[0]['inventory']['agent_version'] == '0.1.0'
            print('Process smoke test passed: consent, enrollment, saved credentials, heartbeat, inventory.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main(sys.argv[1:])
