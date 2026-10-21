# Ubuntu pilot deployment

This deploys the inventory prototype behind Nginx with a Let's Encrypt certificate at `hybridrmm.online`. It is a limited pilot, not a complete production RMM: roles, audit logging, device revocation, and remote support remain future work.

Requirements: Ubuntu 24.04, root access, domain A record pointing to the VM, no incorrect AAAA record, inbound TCP 80/443 allowed, SSH access permitted, and outbound access to Ubuntu packages, GitHub, and Let's Encrypt. Review existing web-server configuration first; this script does not remove existing sites or change the firewall. Port 8080 stays bound to loopback.

From a copy of the repository on the VM:

```bash
sudo bash deploy/ubuntu.sh
```

The script installs packages, clones the source into `/opt/hybridrmm/app`, runs integration tests, creates private random credentials at `/etc/hybridrmm/server.env`, starts the restricted `hybridrmm` systemd service, configures Nginx and HTTPS, and tests enrollment and heartbeat through HTTPS. It preserves existing credentials. It deliberately stops if the app destination already exists so updates cannot discard local changes. On partial failure, inspect completed steps and logs before resuming rather than deleting installed state or credentials.

Useful checks:

```bash
sudo systemctl status hybridrmm nginx --no-pager
sudo journalctl -u hybridrmm -n 50 --no-pager
curl --fail https://hybridrmm.online/healthz
```

Retrieve the admin token privately from the root-only environment file and enter it in the dashboard. Supply only the enrollment token to a consenting inventory agent. Never distribute the admin token to agents. Certificate renewal uses `certbot.timer`; configure a certificate contact email after initial issuance. Back up `/var/lib/hybridrmm` and protect the credentials and backups.

Deployment has not been verified on the target VM until SSH access and the deployment checks succeed. The script itself must also be exercised on Ubuntu; shell syntax checks alone do not establish readiness.

## Browser screen support

The deployment also installs the separate consent-gated signaling service and `/ws` reverse proxy. See [screen support](../docs/SCREEN-SUPPORT.md). Use `deploy/setup-turn.sh` only after HTTPS succeeds to install the optional TURN relay; it requires additional provider/host firewall rules. No native screen capture, remote input, or commands are installed on managed devices. The deployment's public WSS smoke test checks request/rejection flow only; actual cross-network video must be tested separately.


## Current dedicated server

The requested target is `85.215.119.221`. Before deployment, point `hybridrmm.online` to that address and remove any stale IPv6 record. SSH access from the deployment environment must work; the latest attempt on TCP 22 was refused before authentication.

After the base deployment and HTTPS checks succeed, configure TURN with an explicit address:

```bash
RMM_TURN_EXTERNAL_IP=85.215.119.221 bash deploy/setup-turn.sh
```

This is configuration guidance, not evidence that the dedicated server has been accessed or deployed.
