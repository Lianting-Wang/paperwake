#!/usr/bin/env python3
"""Validate maintained files and run offline regression checks."""
import ast
import csv
from datetime import datetime
import hashlib
import subprocess
import sys

from project_config import ROOT, load_config


def main():
    for folder in ('scripts', 'src/scheduler', 'src/tasks', 'tests'):
        for path in (ROOT / folder).glob('*.py'):
            ast.parse(path.read_text(), filename=str(path))
    for path in sorted((ROOT / 'config').glob('*.json')):
        cfg = load_config(path)
        print(f'Config OK: {path.name} ({len(cfg["schedule"])} times/day)', flush=True)
    for folder in ('src/tasks', 'docker'):
        for path in (ROOT / folder).glob('*.sh'):
            subprocess.run(['bash', '-n', str(path)], check=True)
    columns = ['timestamp', 'battery_percent', 'battery_voltage', 'temperature',
               'wifi_rssi', 'ping_loss', 'ping_avg_latency']
    for path in sorted((ROOT / 'data/samples').glob('*.csv')):
        with path.open(newline='') as handle:
            reader = csv.reader(handle)
            if next(reader) != columns:
                raise ValueError(f'CSV header mismatch: {path}')
            count = 0
            for line in reader:
                if len(line) != len(columns):
                    raise ValueError(f'CSV column mismatch: {path}')
                datetime.strptime(line[0], '%Y-%m-%d %H:%M:%S')
                for value in line[1:]:
                    float(value)
                count += 1
        print(f'CSV OK: {path.name} ({count} rows; original order preserved)', flush=True)
    archive = ROOT / 'vendor/bcm2835-1.60.tar.gz'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != '333e7ceee895e910c29098074773ee86bcab4a82c2af0cf083c4533767e52d27':
        raise ValueError('bcm2835 source archive checksum mismatch')
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'), '-v'],
                   cwd=ROOT, check=True)
    print('Offline checks passed. No hardware tasks or shutdown commands were executed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
