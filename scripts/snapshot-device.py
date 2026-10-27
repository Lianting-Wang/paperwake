#!/usr/bin/env python3
"""Download an allowlisted, read-only Raspberry Pi snapshot; never run its tasks."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REMOTE_SCRIPT = r'''
import base64
import hashlib
import json
from pathlib import Path
import subprocess

paths = [
    '/opt/pisched/pisched.py', '/etc/pisched/config.json',
    '/opt/pisched/collect_metrics.py', '/opt/pisched/server_client.py', '/etc/systemd/system/pisched.service',
    '/boot/firmware/config.txt', '/usr/lib/systemd/system/pisugar-poweroff.service',
    '/etc/default/pisugar-poweroff', '/etc/os-release',
    '/home/wlt/IT8951/epd', '/var/lib/pisched/state.json', '/var/lib/pisched/metrics.json',
    '/etc/systemd/system/pisugar-server.service.d/10-local-control.conf',
    '/var/log/pisched/pisched.log',
    '/var/log/pisched/epd.log',
]
# New deployments have a shared planner and an optional downloaded plan cache.
# Their absence on an older device is a valid pre-upgrade snapshot.
paths.extend(name for name in ('/opt/pisched/wake_plan.py', '/opt/pisched/display_model.py', '/var/lib/pisched/wake-plan.json')
             if Path(name).is_file())
display = Path('/home/wlt/IT8951')
if display.is_dir():
    for folder in ('lib', 'singlepage'):
        paths.extend(str(p) for p in sorted((display / folder).rglob('*'))
                     if p.is_file() and not p.is_symlink() and p.suffix in ('.c', '.h'))
    paths.append(str(display / 'Makefile'))
    # Immutable font assets can exceed the log/file ceiling; retain their hashes
    # and dependency manifest, and recover their exact bytes from the release.
    if (display / 'assets/dependencies.json').is_file():
        paths.append(str(display / 'assets/dependencies.json'))
files = []
for name in paths:
    p = Path(name)
    if not p.is_file():
        files.append({'path': name, 'error': 'missing or not a regular file'})
        continue
    if p.stat().st_size > 8 * 1024 * 1024:
        files.append({'path': name, 'error': 'larger than 8 MiB; omitted'})
        continue
    try:
        data = p.read_bytes()
        files.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(),
                      'data': base64.b64encode(data).decode('ascii')})
    except OSError as exc:
        files.append({'path': name, 'error': str(exc)})
commands = [
    ['hostname'], ['date', '-Is'], ['uname', '-a'], ['timedatectl'],
    ['systemctl', 'show', 'pisched.service', '-p', 'ActiveState', '-p', 'Result',
     '-p', 'UnitFileState'],
    ['systemctl', 'show', 'pisugar-server.service', '-p', 'ActiveState', '-p', 'SubState'],
    ['systemd-analyze'],
]
observations = []
for argv in commands:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        observations.append({'command': argv, 'returncode': p.returncode,
                             'stdout': p.stdout, 'stderr': p.stderr})
    except Exception as exc:
        observations.append({'command': argv, 'error': str(exc)})
print(json.dumps({'files': files, 'observations': observations}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='pi', help='SSH host alias (default: pi)')
    parser.add_argument('--output', type=Path, help='New directory; existing paths are refused')
    args = parser.parse_args()
    if args.host.startswith('-') or any(c.isspace() for c in args.host):
        parser.error('host must be a single SSH host name or alias')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = args.output or ROOT / 'backups' / 'device' / stamp
    if output.exists():
        parser.error(f'output already exists: {output}')
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', args.host, 'sudo -n python3 -'],
        input=REMOTE_SCRIPT, capture_output=True, text=True, timeout=180,
    )
    if result.returncode:
        sys.stderr.write(result.stderr)
        return result.returncode
    payload = json.loads(result.stdout)
    # Validate every path and digest before writing anything locally.
    decoded = []
    for entry in payload['files']:
        if 'error' in entry:
            continue
        remote = Path(entry['path'])
        if not remote.is_absolute() or '..' in remote.parts:
            raise ValueError(f'unsafe snapshot path: {remote}')
        data = base64.b64decode(entry['data'], validate=True)
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError(f'hash mismatch: {remote}')
        decoded.append((output / 'rootfs' / str(remote).lstrip('/'), data))
    output.mkdir(parents=True)
    for destination, data in decoded:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    manifest = {
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'host': args.host,
        'files': [{k: v for k, v in entry.items() if k != 'data'} for entry in payload['files']],
        'observations': payload['observations'],
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    missing = [entry['path'] for entry in payload['files'] if 'error' in entry]
    print(f'Saved {len(decoded)} files to {output}')
    if missing:
        print('Omitted: ' + ', '.join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
