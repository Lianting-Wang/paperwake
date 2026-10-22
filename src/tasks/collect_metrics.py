#!/usr/bin/env python3
"""Collect local measurements and render validated server content once."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys

source_modules = Path(__file__).resolve().parents[1] / 'scheduler'
if source_modules.is_dir():
    sys.path.insert(0, str(source_modules))
from pisched import Budget, PiSugarClient, atomic_json, run_command, validate_config, rotate_log
from server_client import validate_display


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


def collect(cfg, event_id):
    budget = Budget(cfg['task']['timeout_sec'])
    data = {'schema_version': 2, 'event_id': event_id,
            'timestamp': datetime.now(timezone.utc).isoformat(), 'values': {}, 'failures': []}
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
    try:
        values['http_latency_ms'] = numeric(os.environ['PISCHED_HTTP_LATENCY_MS'], 0, 60000)
    except (KeyError, ValueError) as exc:
        values['http_latency_ms'] = None
        fail('http_latency', exc)
    log_dir = Path(cfg['log_file']).parent
    try:
        page = validate_display(json.loads(os.environ['PISCHED_PAGE']))
        # Clear inherited row variables; the server supplies this entire page.
        env = {key: value for key, value in os.environ.items() if not key.startswith('PISCHED_PAGE_')}
        env.update(PISCHED_BATTERY='' if values['battery_percent'] is None else str(values['battery_percent']),
                   PISCHED_PAGE_TITLE=page['title'], PISCHED_PAGE_STATUS=page['status'], TZ=cfg['timezone'])
        for index, row in enumerate(page['rows'], 1):
            env[f'PISCHED_PAGE_LABEL_{index}'] = row['label']
            env[f'PISCHED_PAGE_VALUE_{index}'] = row['value']
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
