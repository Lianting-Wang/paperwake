#!/usr/bin/env python3
"""Collect independent measurements and render even when the network is unavailable."""
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys

# The release installs this beside pisched.py; source runs use the scheduler folder.
source_modules = Path(__file__).resolve().parents[1] / 'scheduler'
if source_modules.is_dir():
    sys.path.insert(0, str(source_modules))
from pisched import Budget, PiSugarClient, atomic_json, run_command, validate_config, rotate_log

COLUMNS = ['timestamp', 'battery_percent', 'battery_voltage', 'temperature',
           'wifi_rssi', 'ping_loss', 'ping_avg_latency']


def numeric(value, low, high):
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError('measurement outside valid range')
    return number


def parse_wifi(output):
    match = re.search(r'Signal level[=:]\s*(-?\d+(?:\.\d+)?)\s*dBm', output)
    if not match:
        raise ValueError('Wi-Fi signal unavailable')
    return numeric(match[1], -150, 0)


def parse_ping(output):
    loss = re.search(r'(\d+(?:\.\d+)?)% packet loss', output)
    rtt = re.search(r'(?:rtt|round-trip)[^=]*=\s*[\d.]+/([\d.]+)/', output)
    return (numeric(loss[1], 0, 100) if loss else None,
            numeric(rtt[1], 0, 60000) if rtt else None)


def collect(cfg, event_id):
    budget = Budget(cfg['task']['timeout_sec'])
    data = {'event_id': event_id, 'timestamp': datetime.now(timezone.utc).isoformat(),
            'values': {}, 'failures': []}
    values = data['values']
    def fail(stage, exc):
        data['failures'].append({'stage': stage, 'message': str(exc)[:300]})
    client = PiSugarClient(cfg['pisugar'], budget)
    for field, key, low, high in [('battery_percent', 'battery', 0, 100),
                                  ('battery_voltage', 'battery_v', 0, 10),
                                  ('temperature', 'temperature', -40, 150)]:
        try:
            values[field] = numeric(client.get(key), low, high)
        except Exception as exc:
            values[field] = None
            fail(key, exc)
    try:
        result = run_command(['iwconfig', cfg['network']['interface']], min(3, budget.remaining()))
        if result.returncode:
            raise RuntimeError('iwconfig failed')
        values['wifi_rssi'] = parse_wifi(result.output)
    except Exception as exc:
        values['wifi_rssi'] = None
        fail('wifi', exc)
    values.update(ping_loss=None, ping_avg_latency=None)
    try:
        network = cfg['network']
        result = run_command(['ping', '-n', '-c', str(network['ping_count']), '-i', '0.2', '-W', '1',
                              '-w', str(network['ping_timeout_sec']), network['target']],
                             min(network['ping_timeout_sec'] + 1, budget.remaining()),
                             env=dict(os.environ, LC_ALL='C'))
        values['ping_loss'], values['ping_avg_latency'] = parse_ping(result.output)
        if result.returncode or values['ping_loss'] is None or values['ping_avg_latency'] is None:
            fail('ping', 'measurement unavailable or packet loss prevented RTT measurement')
    except Exception as exc:
        fail('ping', exc)
    log_dir = Path(cfg['log_file']).parent
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / 'network_metrics.csv'
        rotate_log(path)
        empty = not path.exists() or path.stat().st_size == 0
        with path.open('a', newline='') as handle:
            writer = csv.writer(handle)
            if empty:
                writer.writerow(COLUMNS)
            # UTC with offset prevents duplicate/ambiguous timestamps during DST changes.
            writer.writerow([data['timestamp']] + [values.get(key) if values.get(key) is not None else 'NaN'
                                                  for key in COLUMNS[1:]])
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        fail('csv', exc)
    # The renderer uses this exact battery sample, not a second socket query.
    env = dict(os.environ, PISCHED_BATTERY='' if values['battery_percent'] is None else str(values['battery_percent']))
    try:
        result = run_command([cfg['display']['path']], min(cfg['display']['timeout_sec'], budget.remaining()), env=env)
        data['display_returncode'] = result.returncode
        if result.returncode:
            fail('display', f'exit status {result.returncode}')
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            rotate_log(log_dir / 'epd.log')
            with (log_dir / 'epd.log').open('a') as handle:
                handle.write(f"{data['timestamp']} rc={result.returncode}\n{result.output}\n")
        except OSError as exc:
            fail('display_log', exc)
    except Exception as exc:
        fail('display', exc)
    try:
        atomic_json(cfg['metrics_file'], data)
    except (OSError, ValueError) as exc:
        fail('metrics_state', exc)
    for item in data['failures']:
        print(f"{item['stage']} FAILED: {item['message']}", flush=True)
    return 1 if data['failures'] else 0


def main():
    cfg = validate_config(json.loads(os.environ['PISCHED_CONFIG']))
    return collect(cfg, os.environ['PISCHED_EVENT_ID'])


if __name__ == '__main__':
    sys.exit(main())
