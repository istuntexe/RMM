"""Real WebRTC transport tests using synthetic canvas video, not OS capture."""
import asyncio
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from playwright.async_api import async_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent
from server import create_server
from signaling import Signaling


class ScreenSupport(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / 'db.sqlite')
        self.admin, self.enrollment = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        initialize = create_server(('127.0.0.1', 0), self.database, self.admin, self.enrollment)
        initialize.server_close()
        turn_binary = os.environ.get('RMM_TURN_SERVER_PATH') or shutil.which('turnserver')
        if not turn_binary:
            raise RuntimeError('Install coturn or set RMM_TURN_SERVER_PATH to test real relayed media')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            turn_port = listener.getsockname()[1]
        turn_secret = secrets.token_hex(32)
        turn_config = Path(self.temp.name) / 'turn.conf'
        turn_config.write_text(f'''listening-ip=127.0.0.1
relay-ip=127.0.0.1
listening-port={turn_port}
min-port=56000
max-port=57000
realm=localhost
fingerprint
use-auth-secret
static-auth-secret={turn_secret}
no-tls
no-dtls
no-cli
no-rfc5780
allow-loopback-peers
relay-threads=1
pidfile={self.temp.name}/turn.pid
log-file={self.temp.name}/turn.log
''')
        turn_config.chmod(0o600)
        self.turn_log = open(Path(self.temp.name) / 'turn-output.log', 'w')
        self.turn = subprocess.Popen([turn_binary, '-c', str(turn_config)], stdout=self.turn_log, stderr=subprocess.STDOUT)
        async def ready():
            for _ in range(50):
                if self.turn.poll() is not None:
                    raise RuntimeError('Local test TURN process exited; inspect test TURN logs')
                try:
                    connection = socket.create_connection(('127.0.0.1', turn_port), timeout=.1)
                    connection.close()
                    return
                except OSError:
                    await asyncio.sleep(.1)
            raise RuntimeError('Local test TURN did not start')
        await ready()
        self.signal_events = []
        self.signaling = Signaling(self.database, self.admin, [re.compile(r'http://127\.0\.0\.1:\d+')], turn_secret=turn_secret, turn_urls=(f'turn:127.0.0.1:{turn_port}?transport=tcp',))
        original_send = self.signaling.send
        async def traced_send(connection, message):
            self.signal_events.append(message['type'])
            await original_send(connection, message)
        self.signaling.send = traced_send
        self.ws = await self.signaling.start(port=0)
        signal_url = f'ws://127.0.0.1:{self.ws.sockets[0].getsockname()[1]}/ws'
        self.http = create_server(('127.0.0.1', 0), self.database, self.admin, self.enrollment, signal_url)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'
        self.enrolled = agent.request(self.url, '/api/enroll', self.enrollment)
        self.playwright = await async_playwright().start()
        options = {'headless': True, 'args': ['--disable-features=WebRtcHideLocalIpsWithMdns']}
        if os.environ.get('RMM_CHROMIUM_PATH'):
            options['executable_path'] = os.environ['RMM_CHROMIUM_PATH']
        self.browser = await self.playwright.chromium.launch(**options)
        self.contexts = [await self.browser.new_context() for _ in range(2)]
        self.sharer = await self.contexts[0].new_page()
        self.viewer = await self.contexts[1].new_page()
        for context in self.contexts:
            await context.add_init_script('''
                window.testPeers=[];
                const OriginalPeer=window.RTCPeerConnection;
                window.RTCPeerConnection=class extends OriginalPeer {
                    constructor(config){super({...config,iceTransportPolicy:'relay'});window.testPeers.push(this)}
                };
            ''')
        self.errors = []
        for page in (self.sharer, self.viewer):
            page.on('pageerror', lambda error: self.errors.append(str(error)))
        await self.sharer.add_init_script('''
          window.captureCalls=0;
          Object.defineProperty(navigator.mediaDevices, 'getDisplayMedia', {value:async()=>{
            window.captureCalls++;
            const canvas=document.createElement('canvas'); canvas.width=320; canvas.height=240;
            const ctx=canvas.getContext('2d');
            const paint=()=>{ctx.fillStyle='rgb(210,60,90)';ctx.fillRect(0,0,320,240);ctx.fillStyle='white';ctx.fillText(String(Date.now()),10,100)};
            paint(); setInterval(paint,100);
            return canvas.captureStream(10);
          }});
        ''')

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()
        self.ws.close()
        await self.ws.wait_closed()
        await asyncio.to_thread(self.http.shutdown)
        self.http.server_close()
        self.thread.join()
        self.turn.terminate()
        self.turn.wait(timeout=5)
        self.turn_log.close()
        self.temp.cleanup()
        self.assertEqual(self.errors, [], 'Browser JavaScript errors')

    async def join(self):
        await self.sharer.goto(self.url + '/share')
        await self.sharer.get_by_label('Device ID', exact=True).fill(self.enrolled['id'])
        await self.sharer.get_by_label('Device token', exact=True).fill(self.enrolled['device_token'])
        await self.sharer.get_by_role('button', name='Connect', exact=True).click()
        await expect(self.sharer.get_by_role('status')).to_have_text('Ready — waiting for a support request')
        await self.viewer.goto(self.url + '/viewer#' + self.enrolled['id'])
        await self.viewer.get_by_label('Admin token', exact=True).fill(self.admin)
        await self.viewer.get_by_role('button', name='Connect', exact=True).click()
        await expect(self.sharer.locator('#consent')).to_be_visible()
        await expect(self.viewer.get_by_role('status')).to_have_text('Waiting for the device user’s approval')
        self.assertEqual(await self.sharer.evaluate('window.captureCalls'), 0)

    async def test_approval_decoded_video_and_local_stop(self):
        await self.join()
        await self.sharer.get_by_role('button', name='Choose screen and allow').click()
        try:
            await expect(self.viewer.get_by_role('status')).to_have_text('Connected — viewing shared screen', timeout=15000)
        except AssertionError:
            print('Relayed signaling events:', self.signal_events)
            for label, page in [('sharer', self.sharer), ('viewer', self.viewer)]:
                print(label, await page.get_by_role('status').inner_text())
                print(label, await page.evaluate('''async()=>Promise.all(window.testPeers.map(async p=>({connection:p.connectionState,ice:p.iceConnectionState,gathering:p.iceGatheringState,signaling:p.signalingState,local:Boolean(p.localDescription),remote:Boolean(p.remoteDescription),stats:Array.from((await p.getStats()).values()).filter(s=>['candidate-pair','local-candidate','remote-candidate'].includes(s.type)).map(s=>({type:s.type,state:s.state,candidateType:s.candidateType,protocol:s.protocol,requestsSent:s.requestsSent,responsesReceived:s.responsesReceived}))})))'''))
            raise
        await self.viewer.wait_for_function("() => document.getElementById('screen').videoWidth === 320 && document.getElementById('screen').readyState >= 2", timeout=20000)
        pixel = await self.viewer.evaluate('''()=>{const c=document.createElement('canvas');c.width=320;c.height=240;const x=c.getContext('2d');x.drawImage(document.getElementById('screen'),0,0);return Array.from(x.getImageData(20,20,1,1).data)}''')
        self.assertLess(abs(pixel[0] - 210), 15)
        self.assertLess(abs(pixel[1] - 60), 15)
        self.assertLess(abs(pixel[2] - 90), 15)
        candidate_types = await self.viewer.evaluate('''async()=>{const report=await window.testPeers[0].getStats();const transport=Array.from(report.values()).find(s=>s.type==='transport'&&s.selectedCandidatePairId);const pair=report.get(transport.selectedCandidatePairId);return [report.get(pair.localCandidateId).candidateType,report.get(pair.remoteCandidateId).candidateType]}''')
        self.assertEqual(candidate_types, ['relay', 'relay'], 'Video must pass through the real TURN relay')
        Path('build/ui').mkdir(parents=True, exist_ok=True)
        await self.viewer.screenshot(path='build/ui/support-viewer.png', full_page=True)
        await self.sharer.get_by_role('button', name='Stop sharing / disconnect').click()
        await expect(self.viewer.get_by_role('status')).to_have_text('Session ended: stopped')
        self.assertTrue(await self.sharer.evaluate("document.getElementById('screen').srcObject === null"))
        self.assertTrue(await self.viewer.evaluate("document.getElementById('screen').srcObject === null"))
        self.assertEqual(self.signaling.sessions, {})

    async def test_invalid_credentials_cannot_join(self):
        await self.sharer.goto(self.url + '/share')
        await self.sharer.get_by_label('Device ID', exact=True).fill(self.enrolled['id'])
        await self.sharer.get_by_label('Device token', exact=True).fill(self.admin)
        await self.sharer.get_by_role('button', name='Connect', exact=True).click()
        await expect(self.sharer.get_by_role('status')).to_have_text('Support error: unauthorized')
        self.assertEqual(await self.sharer.evaluate('window.captureCalls'), 0)
        self.assertEqual(self.signaling.agents, {})

    async def test_rejection_never_captures_screen(self):
        await self.join()
        await self.sharer.get_by_role('button', name='Reject request').click()
        await expect(self.viewer.get_by_role('status')).to_have_text('Session ended: rejected')
        self.assertEqual(await self.sharer.evaluate('window.captureCalls'), 0)
        self.assertEqual(self.signaling.sessions, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
