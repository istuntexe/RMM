"""Build the native Go agent and record artifact integrity metadata."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--windows-only', action='store_true')
args = parser.parse_args()
compiler = os.environ.get('RMM_GO_PATH') or shutil.which('go')
if not compiler:
    raise SystemExit('Install the official Go 1.27.3 toolchain')
version = subprocess.check_output([compiler, 'version'], text=True).strip()
if not version.startswith('go version go1.27.3 '):
    raise SystemExit('Build requires official Go 1.27.3, got: ' + version)
source = ROOT / 'native-agent'
subprocess.run([compiler, 'mod', 'download'], cwd=source, check=True)
subprocess.run([compiler, 'test', './...'], cwd=source, check=True)
windows = ROOT / 'agent-binaries' / 'windows-amd64'
windows.mkdir(parents=True, exist_ok=True)
environment = dict(os.environ, GOOS='windows', GOARCH='amd64', CGO_ENABLED='0')
subprocess.run([compiler, 'build', '-trimpath', '-buildvcs=false', '-ldflags=-s -w', '-o', str(windows / 'hybridrmm-agent.exe'), '.'], cwd=source, env=environment, check=True)
checksum = hashlib.sha256((windows / 'hybridrmm-agent.exe').read_bytes()).hexdigest()
(windows / 'SHA256SUMS.txt').write_text(checksum + '  hybridrmm-agent.exe\n')
source_hash = hashlib.sha256()
for file in sorted(source.glob('*.go')) + [source / 'go.mod', source / 'go.sum']:
    source_hash.update(file.name.encode() + b'\x00' + file.read_bytes())
(windows / 'build-info.json').write_text(json.dumps({'version': '0.2.0', 'go': 'go1.27.3', 'target': 'windows/amd64', 'signed': False, 'sha256': checksum, 'source_sha256': source_hash.hexdigest(), 'source': 'native-agent/', 'windows_runtime_tested': False}, indent=2) + '\n')
if not args.windows_only and sys.platform.startswith('linux'):
    (ROOT / 'build').mkdir(exist_ok=True)
    subprocess.run([compiler, 'build', '-trimpath', '-buildvcs=false', '-o', str(ROOT / 'build' / 'hybridrmm-agent'), '.'], cwd=source, env=dict(os.environ, GOOS='linux', GOARCH='amd64', CGO_ENABLED='0'), check=True)
# License notices are retained beside the built artifact and included in downloads.
if not (windows / 'THIRD_PARTY_NOTICES.txt').is_file():
    raise SystemExit('Missing retained third-party notices')
print('Built native Windows x64 executable and SHA-256 manifest. Unsigned; Windows lifecycle test remains a separate gate.')
