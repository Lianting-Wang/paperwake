#!/usr/bin/env python3
"""Stage a reviewable release locally; never upload, install, or start services."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from project_config import ROOT, load_config


def prepare(config, epd, output):
    config, epd, output = Path(config), Path(epd), Path(output)
    cfg = load_config(config)
    header = epd.read_bytes()[:20]
    if (len(header) != 20 or header[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(header[18:20], 'little') != 183):
        raise ValueError('epd must be a Linux ARM64 ELF binary built for the Pi')
    if output.exists():
        raise ValueError(f'output already exists; choose a new directory: {output}')
    mappings = [
        (ROOT / 'src/scheduler/pisched.py', 'opt/pisched/pisched.py', 0o755),
        (ROOT / 'src/scheduler/server_client.py', 'opt/pisched/server_client.py', 0o644),
        (ROOT / 'src/scheduler/wake_plan.py', 'opt/pisched/wake_plan.py', 0o644),
        (ROOT / 'src/tasks/collect_metrics.py', 'opt/pisched/collect_metrics.py', 0o755),
        (ROOT / 'deploy/systemd/pisched.service', 'etc/systemd/system/pisched.service', 0o644),
        (config, 'etc/pisched/config.json', 0o644),
        (epd, cfg['display']['path'].lstrip('/'), 0o755),
    ]
    # Keep on-device sources in step with the installed binary, so maintenance
    # builds cannot silently recreate an older driver.
    display_source = ROOT / 'src/display/IT8951'
    display_target = Path(cfg['display']['path']).parent
    for source in sorted(display_source.rglob('*')):
        if source.is_file() and (source.suffix in ('.c', '.h') or source.name == 'Makefile'):
            target = display_target / source.relative_to(display_source)
            mappings.append((source, str(target).lstrip('/'), 0o644))
    for source, _, _ in mappings:
        if not source.is_file():
            raise ValueError(f'missing source: {source}')
    output.mkdir(parents=True)
    files = []
    for source, target, mode in mappings:
        destination = output / 'rootfs' / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(mode)
        files.append({'target': '/' + target, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
    manifest = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'config_source': str(config), 'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
        'device_display_directory': str(Path(cfg['display']['path']).parent), 'files': files,
    }
    (output / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'INSTALL.txt').write_text(
        'This is a local staging directory, not an installed release.\n'
        'Inspect release.json and rootfs/etc/pisched/config.json before installation.\n'
        f"Target: existing Pi Zero 2 / PiSugar 3 device, display path {cfg['display']['path']}.\n"
        'Boot config and PiSugar package-managed units are deliberately excluded.\n'
        'Follow docs/maintenance.md in the source repository for backup, installation,\n'
        'DEBUG mode and rollback steps. No remote commands were executed.\n'
    )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--epd', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    try:
        output = prepare(args.config, args.epd, args.output or ROOT / 'build/releases' / stamp)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f'Prepared {output}; no device files changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
