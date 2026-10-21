# Pre-deployment verification

## Implemented scope

This repository implements opt-in inventory enrollment and heartbeats, SQLite persistence, admin-authenticated inventory listing, and a browser inventory dashboard. It does not yet implement remote desktop, file transfer, command execution, signed updates, device revocation, operator roles, or audit logging. It is not a functional replacement for the original RMM.

## Current-instance results

Validated on Linux with Python 3.12.14 and system Chromium 154.0.8037.57:

- Seven integration tests passed: authentication/input validation, Windows connection-close regression, enrollment and heartbeat, isolated/hashed device credentials, malformed/oversized requests, persistence across server restart, and rejecting non-loopback plaintext agent URLs.
- Five real Chromium tests passed: wrong credentials/empty state; enrollment, offline/online transitions and token storage; treating untrusted inventory as text and clearing rows on failed authentication; failed-network request and retry; desktop screenshot and mobile layout.
- Agent process smoke test passed: consent refusal, enrollment, heartbeat without enrollment token, one online inventory entry.
- Deployment shell syntax, diff whitespace, workflow YAML, and build job dependencies passed checks.

The mobile layout check initially failed because the table overflowed the viewport. It now scrolls within its own container, and the test passes. Deployment script review also found root/group source permissions that would prevent the service account from reading the app; the script now explicitly grants group read/traverse access. That correction still needs validation on Ubuntu.

## Reproduce

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -v
python3 scripts/smoke-agent.py python3 agent.py
# Install Playwright and Chromium as described in README.md, then:
python3 scripts/verify-dashboard.py
bash -n deploy/ubuntu.sh
```

Local browser checks used `RMM_CHROMIUM_PATH=/usr/bin/chromium` to reuse the available browser. Screenshots are under ignored `build/ui/`. CI installs its pinned browser test tooling and uploads screenshots separately.

## Outstanding gates

- GitHub Actions Windows results and executable artifact are unconfirmed: this environment's GitHub API request is blocked. The workflow is configured to test on Windows and exercise the actual packaged executable before artifact upload, but configuration alone does not establish success.
- PowerShell packaging and Windows execution are not validated in this Linux instance.
- Ubuntu service startup, Nginx configuration, public DNS, certificate issuance/renewal, HTTPS deployment smoke tests, and VPS restart recovery have not been exercised on the target VM. SSH was refused from this environment.
- No Authenticode signing, certificate validation on Windows, penetration testing, or production load testing has been performed.

Deployment must remain a limited pilot until these checks and the missing access-control/auditing requirements are addressed. Do not describe all original RMM features or the full production system as ready.
