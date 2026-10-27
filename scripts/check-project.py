#!/usr/bin/env python3
"""Validate maintained files and run offline regression checks."""
import ast
import hashlib
import json
import subprocess
import sys

from project_config import ROOT, load_config


def main():
    for folder in ('scripts', 'src/scheduler', 'src/tasks', 'src/server', 'tests'):
        for path in (ROOT / folder).glob('*.py'):
            ast.parse(path.read_text(), filename=str(path))
    for path in sorted((ROOT / 'config').glob('*.json')):
        cfg = load_config(path)
        print(f'Config OK: {path.name} ({len(cfg["schedule"])} times/day)', flush=True)
    for folder in ('src/tasks', 'docker'):
        for path in (ROOT / folder).glob('*.sh'):
            subprocess.run(['bash', '-n', str(path)], check=True)
    archive = ROOT / 'vendor/bcm2835-1.60.tar.gz'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != '333e7ceee895e910c29098074773ee86bcab4a82c2af0cf083c4533767e52d27':
        raise ValueError('bcm2835 source archive checksum mismatch')
    display = ROOT / 'src/display/IT8951'
    for asset in json.loads((display / 'assets/dependencies.json').read_text())['files']:
        if hashlib.sha256((display / asset['path']).read_bytes()).hexdigest() != asset['sha256']:
            raise ValueError('Renderer dependency checksum mismatch: ' + asset['path'])
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'), '-v'],
                   cwd=ROOT, check=True)
    print('Offline checks passed. No hardware tasks or shutdown commands were executed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
