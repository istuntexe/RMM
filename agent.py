"""Visible, opt-in inventory client. No remote execution or persistence."""
import argparse
import json
import os
import platform
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


def inventory():
    return dict(hostname=platform.node(), os=platform.system(), release=platform.release(), architecture=platform.machine(), agent_version='0.1.0')


def request(server, endpoint, token):
    parsed = urllib.parse.urlsplit(server)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('HTTPS required except for loopback development')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Server URL must not contain credentials, query, or fragment')
    req = urllib.request.Request(server.rstrip('/') + endpoint, data=json.dumps(inventory()).encode(), headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=15) as response:
        return json.load(response)


def run_once(server, state):
    if state.exists():
        stored = json.loads(state.read_text())
        if stored['server'] != server:
            raise ValueError('State belongs to a different server; use a separate state file')
        request(server, '/api/heartbeat', stored['device_token'])
        print('Inventory heartbeat sent', flush=True)
    else:
        token = os.environ.get('RMM_ENROLLMENT_TOKEN', '')
        if not token:
            raise ValueError('RMM_ENROLLMENT_TOKEN required for initial enrollment')
        enrolled = request(server, '/api/enroll', token)
        state.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=state.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(dict(server=server, **enrolled), stream)
            os.replace(name, state)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        print('Device enrolled; credentials saved locally', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True)
    parser.add_argument('--state', type=Path, default=Path('data/device.json'))
    parser.add_argument('--consent', action='store_true', help='Agree to send hostname, OS, release, architecture and agent version')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not args.consent:
        parser.error('--consent is required; this agent reports device inventory')
    try:
        while True:
            run_once(args.server.rstrip('/'), args.state)
            if args.once:
                break
            time.sleep(60)
    except KeyboardInterrupt:
        pass
