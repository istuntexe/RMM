"""Create per-device Windows installer bundles without global credentials."""
import hashlib
import io
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent


def artifact_info(directory):
    directory = Path(directory)
    binary = directory / 'hybridrmm-agent.exe'
    manifest = directory / 'SHA256SUMS.txt'
    if not binary.is_file() or not manifest.is_file() or not (directory / 'THIRD_PARTY_NOTICES.txt').is_file():
        return {'available': False, 'reason': 'Windows agent build is not installed on the server'}
    entries = dict(line.split(None, 1)[::-1] for line in manifest.read_text().splitlines() if len(line.split(None, 1)) == 2)
    expected = entries.get('hybridrmm-agent.exe')
    actual = hashlib.sha256(binary.read_bytes()).hexdigest()
    if actual != expected:
        return {'available': False, 'reason': 'Agent artifact checksum verification failed'}
    return {'available': True, 'sha256': actual, 'version': '0.2.0', 'platform': 'Windows x64', 'signed': False}


def make_bundle(directory, config):
    info = artifact_info(directory)
    if not info['available']:
        raise ValueError(info['reason'])
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(Path(directory) / 'hybridrmm-agent.exe', 'hybridrmm-agent.exe')
        archive.write(Path(directory) / 'THIRD_PARTY_NOTICES.txt', 'THIRD_PARTY_NOTICES.txt')
        archive.writestr('SHA256SUMS.txt', info['sha256'] + '  hybridrmm-agent.exe\n')
        archive.writestr('device.json', json.dumps(config))
        for name in ('install.ps1', 'uninstall.ps1'):
            archive.write(ROOT / 'installers' / name, name)
        archive.writestr('README.txt', 'Hybrid RMM Windows inventory agent\n\nExtract this entire ZIP into a private folder. Open PowerShell as Administrator, change into that folder and run:\n\npowershell -NoProfile -ExecutionPolicy Bypass -File .\\install.ps1\n\nRead the collection notice and type INSTALL to approve. The execution-policy override applies only to this reviewed installer process. The visible Hybrid RMM Inventory Agent service starts automatically at boot and reports device health every minute. Python is not required. This build is unsigned. Do not bypass operating-system malware detections.\n\nThe bundle contains a unique device credential. Install it on exactly one device. Create a fresh bundle for each additional computer. Never publish or share the ZIP. Revoke unused credentials in the admin dashboard.\n\nUninstall using the included uninstall.ps1 as Administrator. No commands, remote input, screen capture, audio or file collection are enabled by this inventory service.\n')
    return result.getvalue()
