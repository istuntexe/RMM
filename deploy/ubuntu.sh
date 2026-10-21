#!/usr/bin/env bash
# Run as root on Ubuntu 24.04 after DNS points at this VM.
set -euo pipefail
umask 027
DOMAIN=hybridrmm.online
APP=/opt/hybridrmm/app
if [[ $EUID -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
. /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then echo 'Ubuntu 24.04 required.' >&2; exit 1; fi
# Stop before changing services if these paths belong to another installation.
for managed in /etc/nginx/sites-available/hybridrmm /etc/systemd/system/hybridrmm.service /etc/systemd/system/hybridrmm-signal.service; do
  if [[ -e $managed ]] && ! head -n 1 "$managed" | grep -q 'Managed by hybridrmm'; then
    echo "Refusing to overwrite existing unmanaged file: $managed" >&2; exit 1
  fi
done
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3 python3-venv nginx certbot python3-certbot-nginx curl
if ! id hybridrmm >/dev/null 2>&1; then useradd --system --home-dir /var/lib/hybridrmm --shell /usr/sbin/nologin hybridrmm; fi
install -d -m 0755 /opt/hybridrmm
if [[ ! -e $APP ]]; then
  git clone --branch main https://github.com/istuntexe/RMM.git "$APP"
else
  echo "$APP already exists; preserve it and update explicitly before rerunning." >&2
  exit 1
fi
# Root owns the source; the service group needs read/traverse access.
chown -R root:hybridrmm "$APP"
chmod -R g+rX,o-rwx "$APP"
cd "$APP"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-signaling.txt
chown -R root:hybridrmm .venv
chmod -R g+rX,o-rwx .venv
.venv/bin/python -m unittest discover -s tests -v
install -d -m 0700 /etc/hybridrmm
if [[ ! -e /etc/hybridrmm/server.env ]]; then
  python3 - <<'PY'
import os, secrets
fd = os.open('/etc/hybridrmm/server.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('RMM_ADMIN_TOKEN=' + secrets.token_urlsafe(32) + '\n')
    stream.write('RMM_ENROLLMENT_TOKEN=' + secrets.token_urlsafe(32) + '\n')
PY
fi
cat > /etc/systemd/system/hybridrmm.service <<'EOF'
# Managed by hybridrmm
[Unit]
Description=Hybrid RMM inventory development pilot
After=network.target
[Service]
User=hybridrmm
Group=hybridrmm
WorkingDirectory=/opt/hybridrmm/app
EnvironmentFile=/etc/hybridrmm/server.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/hybridrmm/app/.venv/bin/python server.py --host 127.0.0.1 --port 8080 --database /var/lib/hybridrmm/rmm.sqlite3
StateDirectory=hybridrmm
StateDirectoryMode=0700
UMask=0077
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/hybridrmm-signal.service <<'EOF'
# Managed by hybridrmm
[Unit]
Description=Hybrid RMM consent-gated screen signaling
After=network.target hybridrmm.service
Requires=hybridrmm.service
[Service]
User=hybridrmm
Group=hybridrmm
WorkingDirectory=/opt/hybridrmm/app
EnvironmentFile=/etc/hybridrmm/server.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/hybridrmm/app/.venv/bin/python signaling.py --database /var/lib/hybridrmm/rmm.sqlite3
StateDirectory=hybridrmm
UMask=0077
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
EOF
# Debian/Ubuntu loads conf.d in the http context.
if [[ -e /etc/nginx/conf.d/hybridrmm-limits.conf ]]; then
  if ! head -n 1 /etc/nginx/conf.d/hybridrmm-limits.conf | grep -q 'Managed by hybridrmm'; then
    echo 'Existing unmanaged Nginx limits configuration; stopping.' >&2; exit 1
  fi
fi
cat > /etc/nginx/conf.d/hybridrmm-limits.conf <<'EOF'
# Managed by hybridrmm
limit_req_zone $binary_remote_addr zone=hybridrmm_api:10m rate=5r/s;
limit_conn_zone $binary_remote_addr zone=hybridrmm_connections:10m;
EOF
cat > /etc/nginx/sites-available/hybridrmm <<'EOF'
# Managed by hybridrmm
server {
    listen 80;
    server_name hybridrmm.online;
    client_max_body_size 16k;
    access_log off;
    location = /ws {
        limit_req zone=hybridrmm_api burst=20 nodelay;
        limit_conn hybridrmm_connections 20;
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 650s;
        proxy_send_timeout 650s;
    }
    location / {
        limit_req zone=hybridrmm_api burst=20 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 15s;
    }
}
EOF
if [[ -e /etc/nginx/sites-enabled/hybridrmm && ! -L /etc/nginx/sites-enabled/hybridrmm ]]; then
  echo 'Existing non-symlink Nginx enabled configuration; stopping.' >&2; exit 1
fi
ln -sfn /etc/nginx/sites-available/hybridrmm /etc/nginx/sites-enabled/hybridrmm
nginx -t
systemctl daemon-reload
systemctl enable --now hybridrmm
systemctl restart hybridrmm
for attempt in {1..20}; do
  if curl --fail --silent http://127.0.0.1:8080/healthz >/dev/null; then break; fi
  if [[ $attempt -eq 20 ]]; then journalctl -u hybridrmm -n 30 --no-pager; exit 1; fi
  sleep 1
done
systemctl enable --now hybridrmm-signal
systemctl restart hybridrmm-signal
systemctl enable --now nginx
systemctl reload nginx
certbot --nginx --non-interactive --agree-tos --register-unsafely-without-email --redirect -d "$DOMAIN"
systemctl enable --now certbot.timer
curl --fail --silent --show-error "https://$DOMAIN/healthz"
# Exercise HTTPS enrollment, repeated heartbeat, and authenticated inventory without printing credentials.
set -a
. /etc/hybridrmm/server.env
set +a
env -u RMM_ADMIN_TOKEN -u RMM_TURN_SECRET runuser -u hybridrmm --preserve-environment -- python3 agent.py --server "https://$DOMAIN" --consent --once --state /var/lib/hybridrmm/smoke-device.json
env -u RMM_ADMIN_TOKEN -u RMM_TURN_SECRET runuser -u hybridrmm --preserve-environment -- python3 agent.py --server "https://$DOMAIN" --consent --once --state /var/lib/hybridrmm/smoke-device.json
python3 - <<'PY'
import json, os, urllib.request
req = urllib.request.Request('https://hybridrmm.online/api/devices', headers={'Authorization': 'Bearer ' + os.environ['RMM_ADMIN_TOKEN']})
with urllib.request.urlopen(req, timeout=15) as response:
    devices = json.load(response)['devices']
assert any(d['online'] for d in devices), 'Expected online smoke-test device'
print('\nHTTPS enrollment, heartbeat, and authenticated inventory passed.')
PY
.venv/bin/python scripts/smoke-signaling.py --url "wss://$DOMAIN/ws" --origin "https://$DOMAIN" --state /var/lib/hybridrmm/smoke-device.json
echo 'Pilot deployed. Credentials are in /etc/hybridrmm/server.env; retrieve privately as root.'
echo 'The smoke-test device is a one-time inventory entry, not a persistent agent.'
