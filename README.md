# Hybrid RMM

Admin dashboard, downloadable Windows inventory agent, automatic device health reporting, and a separate consent-based browser screen-sharing pilot. This is an independent implementation, not a CloakGuard-compatible clone.

## Admin → download → install → monitor

1. Sign in to the dashboard using the admin token from the server's private environment configuration.
2. Choose **Download Windows agent**. Each ZIP creates one pending device with a unique credential, a native Windows x64 executable, `install.ps1`, `uninstall.ps1`, and a checksum manifest. No global admin or enrollment token is included.
3. On the target Windows computer, extract the ZIP into a private directory. Open PowerShell **as Administrator** in that directory and run:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
   ```

4. Read the notice and type **INSTALL**. The installer protects the credential directory and registers the visible **Hybrid RMM Inventory Agent** Windows service, running as **LocalService**, with automatic startup.
5. The agent reports hostname, OS version, architecture, CPU usage, physical memory usage, system-volume disk usage, and uptime every minute. The dashboard refreshes every 30 seconds and shows online/offline/pending/revoked devices, search/filter, device details, and recent CPU readings.
6. Use **Revoke** to invalidate a retired device's credential. This blocks reports and new screen sessions; it does not uninstall software. Run the included uninstaller locally to remove the service. The protected configuration remains for deliberate preservation/removal.

Use a fresh download for every device. A ZIP contains that device's credential: never publish it or reuse it across computers. Python and an open browser are not required on monitored Windows devices. The process-only execution-policy override above does not change persistent policy. The executable is **unsigned**; Authenticode signing and reputation are not established. Windows 10/Server 2016 or newer, x64, is the build baseline.

## Upgrade the existing VPS

The older deployed pilot does not gain this UI until its source is upgraded. On the VPS in your working Bitvise session:

```bash
cd /root/RMM-deploy
git pull --ff-only
bash scripts/upgrade-vps.sh
```

The upgrade helper refuses local source changes, verifies a fast-forward update, preserves a private database/environment backup, installs dependencies, checks artifact integrity, runs integration tests, and restarts the existing managed services. It preserves coturn and Nginx configuration. A failed check stops the upgrade and leaves the backup for diagnosis; do not blindly delete installed state or overwrite credentials. It has been syntax-checked, not run on your VPS from this environment.

Afterward open `https://hybridrmm.online`. Get the admin token privately from `/etc/hybridrmm/server.env` on the VPS; do not paste that file into chat. Run the installer on a single test computer and verify reporting, restart, and uninstall before wider rollout.

For a fresh Ubuntu 24.04 host, see [deployment](deploy/README.md). The server's public origin is configured with `RMM_PUBLIC_URL` (defaults to `https://hybridrmm.online` in the CLI). It is never inferred from an untrusted Host header.

## Run the server locally

Python 3.12 or newer:

```bash
python3 -m pip install -r requirements-signaling.txt
export RMM_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export RMM_ENROLLMENT_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export RMM_PUBLIC_URL=http://127.0.0.1:8080
python3 server.py
```

The admin and legacy enrollment tokens must differ and contain at least 32 characters. Keep the same private tokens and database when restarting. Backends bind loopback and must sit behind an HTTPS reverse proxy for remote use. The Linux server uses Python; no Go compiler is needed in deployment because the unsigned Windows artifact is included under `agent-binaries/windows-amd64/`. Its SHA-256 is verified before an installer ZIP is generated.

The original `agent.py` remains as a manual, legacy enrollment client for testing/backward compatibility. Dashboard-provisioned native agents need only their private `device.json`; they never receive the global enrollment token.

## Native agent source and builds

Source is under `native-agent/`. Build with official **Go 1.27.3** and Python:

```bash
python3 scripts/build-native.py
```

Set `RMM_GO_PATH` if the compiler is outside PATH. The build verifies Go module checksums through the normal Go module/sum database mechanisms, runs native Go tests, cross-builds the Windows executable, and writes its checksum and source digest. On Linux it also builds `build/hybridrmm-agent` for functional testing. CGO is disabled. The Windows executable and manifest are deliberately retained in Git so the admin download works after a normal VPS checkout. They contain no server-specific credentials. Do not edit binaries or checksum expectations to bypass failed verification.

The historical PyInstaller helper is not the dashboard's native agent build. GitHub Actions now builds native agents, runs Windows/Linux process tests, and includes a real Windows service lifecycle test on an isolated runner. The [Windows/Linux CI run](https://github.com/istuntexe/RMM/actions/runs/39538861703) passed, including native Windows reporting and the real service installation/restart/uninstall test. The committed executable was cross-built locally; its initial metadata remains conservative about testing that exact byte sequence. Test the delivered bundle on one target device before wider deployment.

## Validate

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -v
python3 scripts/smoke-agent.py python3 agent.py
# After the native build, on Linux:
python3 scripts/smoke-native-agent.py build/hybridrmm-agent
# Optional development browser tooling:
python3 -m pip install playwright==1.58.0
python3 -m playwright install chromium
python3 scripts/verify-dashboard.py
# Install coturn first for real local relay transport tests:
python3 scripts/verify-support.py
```

Browser tests exercise real sign-in, ZIP download, pending→online transitions, health details, revocation, activity, search/filter, logout, safe rendering, network recovery, and desktop/mobile layout. Screenshots under ignored `build/ui/` use test inventory, not evidence of a Windows installation. The native smoke test runs a real OS agent against the Python server using a dashboard-generated credential. See [verification](docs/VERIFICATION.md) for passed versus outstanding gates.

## API and storage

Admin bearer token required:

- `GET /api/devices`: fleet inventory/status.
- `GET /api/devices/<id>`: device detail and up to 60 recent health samples.
- `GET /api/agents`: current artifact availability/checksum.
- `POST /api/agents/windows/download` with `{}`: private per-device installer ZIP.
- `POST /api/devices/<id>/revoke` with `{}`: invalidate one device.
- `GET /api/audit`: latest 100 administration events.

Device credential required: `POST /api/heartbeat`. Legacy global enrollment token: `POST /api/enroll`. Public readiness: `GET /healthz`.

Inventory strings are bounded and health values must be finite, nonnegative numbers. CPU must be 0–100%; used memory/disk cannot exceed total. The server retains at most 1,440 health samples per reporting device. Schema migration adds revocation/history/audit tables without dropping existing inventory. Device credentials are hashed in SQLite; inventory and metrics are plaintext, so protect the database and backups. Tokens, passwords, and download contents are not written to application audit logs.

## Scope

This is a device-monitoring foundation. Named operator roles, multi-tenancy, alert delivery, software inventory/patching, remote commands/scripts, native remote keyboard/mouse, file transfer, signed updates, and code signing remain unimplemented. Shared-admin-key authentication is not a full operator identity system. No security-tool interference, decoys, or evasion is included.

[Browser screen support](docs/SCREEN-SUPPORT.md) remains separate: it uses a supported browser and requires the device user's approval and screen picker for each view-only session. The native service does not capture screens, audio, input, browsing activity, or user files.

Each cloud task has an isolated checkout. Work in the existing checkout; do not create Git worktrees unless explicitly requested.
