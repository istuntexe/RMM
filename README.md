# RMM inventory foundation

A new server and matching opt-in inventory agent, written in Python 3.12 using only the standard library. This is an independent implementation, not a reconstruction of CloakGuard or a client compatible with its API.

## Run locally

From `/workspace/RMM`:

```bash
export RMM_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export RMM_ENROLLMENT_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 server.py
```

The server binds loopback port 8080. Open its root page locally to use the inventory dashboard. Enter your admin token there. Keep the tokens private; never commit them. Admin and enrollment tokens must differ and be at least 32 characters long. Restart the server with the same tokens if you want existing admin and enrollment access to continue. Existing device credentials remain valid in the SQLite database.

In a shell where `RMM_ENROLLMENT_TOKEN` is available:

```bash
python3 agent.py --server http://127.0.0.1:8080 --consent --once
```

Omit `--once` to report every 60 seconds in the foreground; Ctrl-C stops the agent. Each device should use its own state file (`--state PATH`). Initial enrollment uses the shared enrollment token; subsequent heartbeats use a unique device credential saved in the state file. On Windows, additionally restrict the state file's ACL to its service account before deployment; POSIX file modes do not establish Windows ACLs.

The only collected fields are hostname, operating system, OS release, machine architecture, and agent version. No username, browsing history, screen, audio, or user files are collected. No service, autorun, or other persistence is installed.

## API

- `GET /healthz`: readiness response.
- `POST /api/enroll`: enrollment bearer token, inventory JSON; returns device ID and a one-time device credential.
- `POST /api/heartbeat`: device bearer credential, inventory JSON.
- `GET /api/devices`: admin bearer token; lists inventory and last-seen status. “Online” means a heartbeat within 180 seconds.

Inventory JSON must contain exactly the five fields listed above as strings of at most 256 characters. Request bodies are limited to 16 KiB. Device credentials are hashed in SQLite and omitted from device listings. Inventory itself is stored as plaintext in the database, so restrict access to the database and its backups.

## Validate

```bash
python3 -m unittest discover -s tests -v
```

Integration tests use an ephemeral local HTTP server and temporary database, exercising enrollment, persisted agent state, heartbeat updates, dashboard delivery, access separation, invalid input, and HTTPS enforcement.

## Scope and deployment

This is a development prototype, not an Internet-facing production RMM. Before remote deployment, add a supported application server/reverse proxy with HTTPS, rate limits, operator identity and roles, audit logs, device revocation and enrollment-token rotation, Windows credential storage, and deployment tests. The Python development HTTP server is not intended for direct Internet exposure. Agents reject non-loopback plain HTTP and do not follow redirects carrying credentials.

Remote desktop, file transfer, command execution, signed updates, packaging, and code signing are not implemented. A later Windows build must be tested on Windows and signed only after its final contents and publisher identity are established. No existing CloakGuard credentials or download tokens are reused.

Each cloud task already has an isolated checkout. Work in the existing checkout; do not create Git worktrees unless explicitly requested.

## Verify the dashboard in a browser

The dashboard is an inventory page with admin-token authentication, manual refresh, online/offline status, and a horizontally scrollable table on small screens. It is not a complete remote-support console.

```bash
python3 -m pip install playwright==1.58.0
python3 -m playwright install chromium
python3 scripts/verify-dashboard.py
```

These five real-browser tests cover wrong credentials, empty state, enrollment, status transitions after heartbeat, token storage, untrusted inventory text, clearing rows after authentication failure, network error recovery, and mobile layout. Screenshots are saved under ignored `build/ui/`. Where an existing Chromium installation is used, set `RMM_CHROMIUM_PATH` to its executable path. Browser tooling is for development only, not a runtime server dependency. CI gates the executable build on both API and browser tests.

## Build the Windows executable

GitHub Actions runs integration and real-process smoke tests on Ubuntu and Windows, then builds and tests an **unsigned Windows x64 executable**. After a successful run, download `hybridrmm-agent-windows-x64-unsigned` from the repository's Actions tab. It includes the executable, `SHA256SUMS.txt`, and build metadata. Workflow creation alone does not establish that the remote build succeeded.

To build on Windows yourself, install Python 3.12 x64, open PowerShell in the checkout, and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1
```

This invocation applies only to the reviewed local build script; it does not change the machine's persistent execution policy. The script creates `.venv-build`, installs pinned PyInstaller with standard TLS/package verification, runs tests, builds the console executable, and tests the packaged program against a temporary local server. Build dependencies are pinned at the PyInstaller version level, not fully locked transitively; bit-identical reproducibility is not guaranteed.

Run the packaged agent with an enrollment token supplied privately in its environment:

```powershell
.\dist\hybridrmm-agent.exe --server https://hybridrmm.online --consent --once --state "$env:LOCALAPPDATA\HybridRMM\device.json"
```

Use this URL only after the server is deployed and HTTPS is verified. Omit `--once` for foreground reporting every minute. No administrator privileges are required for basic inventory. Protect the credential file with Windows ACLs before deployment. The executable has no embedded server credentials and is not an installer or Windows service. Signing and timestamping must occur after the final executable is produced; regenerate the checksum after signing because signing changes the file.
