"""Verify public signaling authentication and rejection, without screen capture."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from websockets.asyncio.client import connect


async def check(url, origin, state):
    parsed = urlsplit(url)
    if parsed.scheme != 'wss' and not (parsed.scheme == 'ws' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('Use WSS except on loopback')
    token = os.environ.get('RMM_ADMIN_TOKEN', '')
    if not token:
        raise ValueError('RMM_ADMIN_TOKEN is required')
    device = json.loads(state.read_text())
    async def receive(connection):
        return json.loads(await asyncio.wait_for(connection.recv(), timeout=10))
    async with connect(url, origin=origin, open_timeout=10) as agent:
        await agent.send(json.dumps(dict(type='auth', role='agent', device_id=device['id'], token=device['device_token'])))
        assert (await receive(agent))['type'] == 'ready'
        async with connect(url, origin=origin, open_timeout=10) as viewer:
            await viewer.send(json.dumps(dict(type='auth', role='viewer', device_id=device['id'], token=token)))
            waiting, request = await receive(viewer), await receive(agent)
            assert waiting['type'] == 'waiting' and request['type'] == 'request'
            assert waiting['session_id'] == request['session_id']
            await agent.send(json.dumps(dict(type='reject', session_id=request['session_id'])))
            assert (await receive(viewer))['reason'] == 'rejected'
            assert (await receive(agent))['reason'] == 'rejected'
    print('Signaling smoke passed: separate credentials, request relay, consent rejection. No screen captured.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(check(args.url, args.origin, args.state))
