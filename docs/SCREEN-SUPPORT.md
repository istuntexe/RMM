# Consent-based screen sharing

This implements the signaling and video portion of the supplied architecture with the current Python server and browser frontend. It uses browser WebRTC encoding instead of a nonfunctional raw-RGBA Go/Pion placeholder. No React migration or native Go capture agent was performed. Native keyboard/mouse input, remote commands, scripts, and file transfer remain unimplemented.

## How it works

1. Enroll the inventory agent against the intended server. Its private state file contains device `id`, `device_token`, and server origin.
2. On that device, open the server's `/share` page in Chrome/Edge over HTTPS (or loopback HTTP for development).
3. Load the local device state JSON using the file picker, or enter the device ID and device token. The file is parsed locally and never uploaded. The credential authenticates signaling; it is not stored in browser storage. Never give this page an admin token.
4. Click **Connect**. The device user must keep this page open. The inventory agent alone does not enable screen access.
5. The operator opens **View screen** beside the enrolled device in the inventory dashboard, enters the admin token, and clicks **Connect**. The device page must already be connected; offline and busy devices return a clear error.
6. The device page displays the request. Click **Choose screen and allow**, then choose the intended screen or window in the browser picker. Rejecting does not invoke capture. Neither audio nor keyboard/mouse access is requested.
7. Either side can stop. Closing the page or using the browser's stop-sharing control also ends the session. Requests expire after 30 seconds; sessions after 10 minutes.

This is browser screen sharing, not unattended native remote desktop. The server authenticates possession of an enrolled device credential; it cannot prove a browser is physically running on that device. Protect the state file and use it only on the enrolled computer. Operator identity is currently a shared admin token, not a named-user role system.

## Local development

Install `requirements-signaling.txt`. The HTTP server and signaling process must share the same database and admin token. In the server's process environment set:

```bash
export RMM_SIGNAL_URL=ws://127.0.0.1:8081/ws
export RMM_ALLOWED_ORIGINS=http://127.0.0.1:8080
```

Run `python3 server.py` first to create the database. In a second shell with the same configuration and credentials, run `python3 signaling.py`. Local development uses direct host candidates unless TURN variables are configured. Real networks may require TURN; a successful local direct connection does not establish remote readiness.

In deployment, leave `RMM_SIGNAL_URL` unset so the browser uses same-origin `wss://hybridrmm.online/ws`. The deployment script installs a pinned WebSocket dependency into a venv, adds a restricted signaling systemd service, and adds an exact `/ws` Nginx proxy. Only loopback ports 8080 and 8081 are used by backend processes. Allowed browser origins default to `https://hybridrmm.online` and are checked at handshake.

## TURN

The optional `deploy/setup-turn.sh` configures coturn on Ubuntu 24.04 after the HTTPS server deployment succeeds. Review it before running as root:

```bash
sudo env RMM_TURN_EXTERNAL_IP=85.215.119.221 bash deploy/setup-turn.sh
```

It deliberately refuses unmanaged coturn configuration, preserves existing compatible credentials, generates the master secret privately in `/etc/hybridrmm/server.env`, and gives coturn the same secret through a protected configuration. The master secret is never sent to the agent or browser. After consent, signaling issues temporary timestamped usernames and HMAC-SHA1 passwords in the coturn REST format. Password expiry is session lifetime plus a 60-second allowance; keep the VM clock synchronized.

The script copies the certificate/key into a coturn-readable directory and installs a restricted renewal hook. It requires an explicit public IPv4 through `RMM_TURN_EXTERNAL_IP`; the current dedicated server is `85.215.119.221`. Review the external/private mapping if the server uses NAT. Open 3478 TCP/UDP, 5349 TCP, and relay ports 49160–49260 UDP in both the host and provider firewall. Do not open the entire 49152–65535 range by default. Private, loopback, link-local, and multicast peers are blocked in deployment. Never use the loopback allowance from the test fixture in public coturn configuration.

Coturn startup, public relay connectivity, TLS certificate access/renewal, firewall rules, and cross-network video still require checks on the actual VPS. The TURN script has only been syntax-checked here.

## Tests and evidence

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -v
# Development dependencies only:
python3 -m pip install playwright==1.58.0
python3 -m playwright install chromium
# Linux: install coturn using your package manager before this test.
python3 scripts/verify-support.py
```

Signaling integration tests exercise real WebSockets, separate credentials, allowed origins, per-device/session isolation, consent gating, TURN HMAC credentials, rejection, repeated requests, expiry, disconnection, and audit entries. Unsupported command/control messages are rejected.

The browser transport test starts its own temporary, loopback-only coturn process, forces relay-only WebRTC over TURN/TCP, and uses a synthetic canvas capture provider. It verifies actual decoded video pixels at the receiver, consent timing, stopping at both peers, and rejection without capture. It does not prove native Windows capture or a real browser picker works. No public service or screen recording is started by the fixture. Local development can select installed tools with `RMM_CHROMIUM_PATH` and `RMM_TURN_SERVER_PATH`.

The deployment script also runs `scripts/smoke-signaling.py` over public WSS to verify authentication, request relay, and rejection with a temporary inventory device. That check never captures a screen and does not replace a public media test.

## Boundaries

Session requests, acceptance, and termination are stored in SQLite `support_audit`. Tokens, SDP, ICE candidate addresses, video frames, and user screen content are not stored there. The signaling server bounds message sizes, per-connection message rate, duplicate connections, and session duration. Nginx limits handshakes/connections. These controls are not a substitute for named operator accounts, per-device access policies, revocation, comprehensive audit review, or production load/security testing.
