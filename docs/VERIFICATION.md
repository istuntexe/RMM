# Verification status

## Monitoring workflow implemented

Admin sign-in → unique Windows installer ZIP → explicit local installation → visible automatic Windows service → periodic device health reporting → dashboard monitoring/detail/history → credential revocation.

The downloadable unsigned Windows x64 executable is built from `native-agent/`, not the original CloakGuard binary. It does not need Python or a browser. `agent-binaries/windows-amd64/build-info.json` identifies its toolchain and source digest. The retained artifact was cross-compiled on Linux. The same source also passed native Windows builds, process tests and service lifecycle checks in GitHub Actions; the initial artifact metadata conservatively distinguishes the retained cross-built bytes from the rebuilt CI artifact.

## Current-instance evidence

- 20 Python API/signaling tests passed, including provisioning, unique/scoped credentials, health/history, invalid metrics, artifact integrity, revocation, migration-compatible operations, and the previous inventory/signaling cases.
- Six real Chromium admin dashboard tests passed: authentication; actual installer download and pending→online flow; health/details/history and revocation; audit entries; search/filter/offline transitions/logout; no browser credential storage; untrusted text rendering; network recovery; desktop/mobile layout and agent page.
- Three native Go tests passed locally on Linux and were also run in the successful Windows/Linux CI jobs: unsafe configuration rejection, actual OS metrics/authenticated heartbeat, and rejecting credential-bearing redirects.
- A real native Linux process passed the dashboard-generated credential→health→online→revocation smoke test. Continuous monitoring was separately exercised: the same device sent a second real health report after 60.2 seconds.
- The legacy Python agent smoke test and real WebSocket/CLI startup were validated earlier. They must remain passing after changes.
- Browser view-only support was validated with synthetic video and a real loopback coturn relay. Its regression suite must remain passing after shared server changes.
- Deployment/upgrade shell scripts, JavaScript, and workflow YAML are checked locally; shell syntax checks are not target deployment tests.

The Windows Go source compiles, including Windows API metric collection and service integration. Linux process tests do not prove Windows service behavior.

## Windows CI evidence

[Run 39538861703](https://github.com/istuntexe/RMM/actions/runs/39538861703) completed successfully for source commit `d5e1999`. Both native-agent matrix jobs succeeded, including Windows PowerShell parsing, real OS health reporting, installation as LocalService, automatic service configuration, restart reporting, and uninstall. Public job summaries were readable; authenticated step logs and the artifact bytes were not retrieved here. The first run exposed an installer quoting issue; the corrected run passed.

## Outstanding gates
- Manually test installation on a consenting Windows test device, including credential ACLs, initial report, service restart/boot, offline behavior, revocation, and uninstall. Do not claim reboot recovery from configuration alone.
- Upgrade the user's VPS and verify the new dashboard/download there. The user supplied logs establish deployment of the earlier pilot, not this revision. SSH access from this cloud environment has remained refused.
- Verify public TURN video and real Windows browser screen picking separately; the native inventory service is not the screen-sharing agent.
- Authenticode signing, reputation, production load/security testing, named operator authorization, and a complete RMM feature set remain outstanding.

The new upgrade helper preserves private backups and refuses local source edits; it has not been executed on the target VPS here. Do not claim the user's live UI has changed until the update is performed and checked.
