# Pre-deployment verification

## Implemented scope

- Opt-in device inventory, enrollment, heartbeats, and SQLite persistence.
- Authenticated browser inventory dashboard, including desktop/mobile layout.
- Authenticated WebSocket signaling and view-only browser WebRTC screen sharing with explicit per-session approval and the browser's screen picker.
- Session audit events, timeouts, stopping/rejection, and short-lived coturn credentials.
- Windows inventory executable packaging workflow and Ubuntu deployment scripts.

This is not a functional clone of CloakGuard. Native Go capture, keyboard/mouse control, remote commands/scripts, file transfer, signed updates, named operator roles, and device revocation are not implemented. Session audit events exist; a comprehensive operator audit system does not.

## Current-instance results

Validated on Linux with Python 3.12.14, websockets 16.0, system Chromium 154.0.8037.57, and coturn 4.6.1:

- **13 API/signaling integration tests passed:** authentication/input validation, connection cleanup, enrollment/heartbeat, credential isolation/hashing, malformed/oversized requests, restart persistence, rejecting remote plaintext URLs; WebSocket origin checks, separate role credentials, consent, session isolation, TURN HMAC, rejection/retry, expiry, disconnects, and audit records.
- **Five real Chromium dashboard tests passed:** invalid credentials/empty state; enrollment and online/offline transitions; no browser token storage; safe rendering and clearing rows after failed authentication; network error recovery; desktop and mobile layout.
- **Three real Chromium screen-support tests passed:** relay-only WebRTC decoded expected synthetic video pixels, both selected candidates were relay candidates through local coturn, stopping cleared both players; invalid credentials rejected; request rejection never invoked capture.
- **Agent process smoke test passed:** consent refusal, enrollment, saved credentials without enrollment token, heartbeat, one online device.
- **Separate server/signaling CLI startup passed:** HTTP readiness, real enrollment, WebSocket authentication, request relay, rejection, and graceful process cleanup.
- Deployment shell syntax, JavaScript syntax, workflow YAML/build gates, and diff whitespace checks passed.

The screen-support browser fixture intentionally replaces OS screen capture with synthetic canvas video. It exercises actual WebRTC encoding/decoding, real TURN authentication and relay, signaling, consent, and stop logic. It does not validate a real OS screen picker, native Windows screen capture, public TURN/TLS, or remote keyboard/mouse control.

Local coturn and its required libraries were obtained from Debian's signed package metadata using normal APT verification, extracted under `/workspace/.tools/coturn`, and used only as a temporary loopback test fixture. The fixture's loopback peer allowance must never be copied to the public TURN configuration. The first media test with direct candidates failed in this cloud browser; using a real TURN/TCP relay established the connection and produced decoded video. No verification was disabled to obtain the test dependencies.

## Fixes found during verification

- Explicit SQLite closing prevents the Windows test database deletion error.
- The inventory table now scrolls within its container on mobile.
- Root-owned source and venv group permissions now allow the restricted service account to read the application; this deployment correction still needs an Ubuntu runtime check.
- ICE/SDP handling queues candidates until remote description is ready and waits for WebSocket authentication plus device approval before offers.
- Agent and operator credentials are distinct; TURN's master secret is not sent to clients.

## Outstanding gates

- Windows GitHub Actions build result and executable artifact remain unconfirmed: this environment's GitHub API request is blocked. The workflow tests Windows and the packaged executable, but configuration alone does not establish success.
- Real browser screen-picker and stopping behavior on the target Windows desktop require a manual test.
- Ubuntu systemd/Nginx startup, public DNS/HTTPS, certificate issuance and renewal, public TURN/TLS, provider/host firewall rules, and cross-network video have not been verified on the VPS. SSH has been refused from this environment.
- TURN deployment script is syntax-checked, not verified on Ubuntu.
- No Authenticode signing, production load testing, or independent security review was performed.

Remain in limited-pilot scope until those gates and the missing production access-control requirements are satisfied. Refer to README.md and SCREEN-SUPPORT.md for runnable checks. Do not describe all original RMM features or the full production system as ready.
