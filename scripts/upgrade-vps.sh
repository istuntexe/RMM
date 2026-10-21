#!/usr/bin/env bash
# Upgrade a managed pilot without replacing its private configuration or database.
set -euo pipefail
umask 077
APP=/opt/hybridrmm/app
if [[ $EUID -ne 0 ]]; then echo 'Run as root on the deployed VPS.' >&2; exit 1; fi
cd "$APP"
for required in /etc/hybridrmm/server.env /var/lib/hybridrmm/rmm.sqlite3; do
  [[ -f $required ]] || { echo "Required managed deployment file missing: $required" >&2; exit 1; }
done
if [[ -n $(git status --porcelain --untracked-files=normal) ]]; then
  echo 'Local source changes found. Preserve/review them before upgrading.' >&2; exit 1
fi
git fetch origin main
git merge-base --is-ancestor HEAD FETCH_HEAD || { echo 'Upgrade is not a fast-forward; stopping.' >&2; exit 1; }
BACKUP_DIR="/var/backups/hybridrmm/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP_DIR"
cp --preserve=mode /etc/hybridrmm/server.env "$BACKUP_DIR/server.env"
trap 'echo "Upgrade failed. Preserve $BACKUP_DIR and inspect the failing command before resuming services." >&2' ERR
systemctl stop hybridrmm-signal hybridrmm
python3 - "$BACKUP_DIR" <<'PY'
import pathlib, sqlite3, sys
from contextlib import closing
source = pathlib.Path('/var/lib/hybridrmm/rmm.sqlite3')
with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(pathlib.Path(sys.argv[1]) / 'rmm.sqlite3')) as dst:
    src.backup(dst)
PY
# If a later check fails, preserve the backup and report the error for diagnosis.
git merge --ff-only FETCH_HEAD
.venv/bin/python -m pip install -r requirements-signaling.txt
chown -R root:hybridrmm "$APP"
chmod -R g+rX,o-rwx "$APP"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python - <<'PY'
from provisioning import artifact_info
info = artifact_info('agent-binaries/windows-amd64')
assert info['available'], info['reason']
print('Windows agent artifact integrity verified.')
PY
systemctl start hybridrmm hybridrmm-signal
for attempt in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8080/healthz >/dev/null; then break; fi
  if [[ $attempt -eq 30 ]]; then echo "Startup failed. Backup preserved at $BACKUP_DIR" >&2; exit 1; fi
  sleep 1
done
systemctl is-active --quiet hybridrmm hybridrmm-signal
curl --fail --silent --show-error https://hybridrmm.online/healthz
echo
echo "Upgrade finished. Backup preserved at $BACKUP_DIR"
echo 'Sign in at https://hybridrmm.online and choose Download Windows agent.'
