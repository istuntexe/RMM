#!/usr/bin/env bash
# Optional Ubuntu TURN setup, after HTTPS inventory deployment succeeds.
set -euo pipefail
umask 077
if [[ $EUID -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
. /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then echo 'Ubuntu 24.04 required.' >&2; exit 1; fi
for path in /etc/turnserver.conf /etc/letsencrypt/renewal-hooks/deploy/hybridrmm-turn.sh; do
  if [[ -e $path ]] && ! head -n 2 "$path" | grep -q 'Managed by hybridrmm'; then
    echo "Refusing to overwrite unmanaged configuration: $path" >&2; exit 1
  fi
done
for required in /etc/hybridrmm/server.env /etc/letsencrypt/live/hybridrmm.online/fullchain.pem /etc/letsencrypt/live/hybridrmm.online/privkey.pem; do
  if [[ ! -f $required || -L /etc/hybridrmm/server.env ]]; then echo 'Existing HTTPS deployment and private environment file required.' >&2; exit 1; fi
done
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y coturn
install -d -m 0750 -o root -g turnserver /etc/coturn/tls
install -m 0644 -o root -g turnserver /etc/letsencrypt/live/hybridrmm.online/fullchain.pem /etc/coturn/tls/fullchain.pem
install -m 0640 -o root -g turnserver /etc/letsencrypt/live/hybridrmm.online/privkey.pem /etc/coturn/tls/privkey.pem
python3 - <<'PY'
import os, re, secrets
from pathlib import Path
path = Path('/etc/hybridrmm/server.env')
text = path.read_text()
values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
secret = values.get('RMM_TURN_SECRET') or secrets.token_hex(32)
if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', secret):
    raise ValueError('Existing TURN secret format needs manual review; no values changed')
urls = 'turn:hybridrmm.online:3478?transport=udp,turn:hybridrmm.online:3478?transport=tcp,turns:hybridrmm.online:5349?transport=tcp'
if values.get('RMM_TURN_URLS') not in (None, urls):
    raise ValueError('Existing TURN URL configuration needs manual review; no values changed')
additions = {'RMM_TURN_SECRET': secret, 'RMM_TURN_URLS': urls}
with path.open('a') as stream:
    if text and not text.endswith('\n'):
        stream.write('\n')
    for key, value in additions.items():
        if key not in values:
            stream.write(key + '=' + value + '\n')
os.chmod(path, 0o600)
config = f'''# Managed by hybridrmm
listening-port=3478
tls-listening-port=5349
# This VM was supplied with this public IPv4; review mapping if the VM changes.
external-ip=150.241.106.58
min-port=49160
max-port=49260
fingerprint
use-auth-secret
static-auth-secret={secret}
realm=hybridrmm.online
total-quota=100
user-quota=4
stale-nonce=600
no-cli
no-multicast-peers
no-rfc5780
no-tlsv1
no-tlsv1_1
cert=/etc/coturn/tls/fullchain.pem
pkey=/etc/coturn/tls/privkey.pem
denied-peer-ip=0.0.0.0-0.255.255.255
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=100.64.0.0-100.127.255.255
denied-peer-ip=127.0.0.0-127.255.255.255
denied-peer-ip=169.254.0.0-169.254.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
denied-peer-ip=::1
denied-peer-ip=fc00::-fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff
denied-peer-ip=fe80::-febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff
'''
path = Path('/etc/turnserver.conf')
path.write_text(config)
os.chmod(path, 0o640)
PY
chown root:turnserver /etc/turnserver.conf
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/hybridrmm-turn.sh <<'EOF'
#!/bin/sh
# Managed by hybridrmm
set -eu
[ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/hybridrmm.online ] || exit 0
install -m 0644 -o root -g turnserver "$RENEWED_LINEAGE/fullchain.pem" /etc/coturn/tls/fullchain.pem
install -m 0640 -o root -g turnserver "$RENEWED_LINEAGE/privkey.pem" /etc/coturn/tls/privkey.pem
systemctl restart coturn
EOF
chmod 0750 /etc/letsencrypt/renewal-hooks/deploy/hybridrmm-turn.sh
# The package's Ubuntu systemd service reads /etc/turnserver.conf.
systemctl enable --now coturn
systemctl restart coturn hybridrmm-signal
systemctl is-active --quiet coturn hybridrmm-signal
echo 'TURN configured. Allow 3478 TCP/UDP, 5349 TCP, and 49160:49260 UDP in host/provider firewalls.'
echo 'Verify public relay-only video and certificate renewal before claiming remote readiness.'
