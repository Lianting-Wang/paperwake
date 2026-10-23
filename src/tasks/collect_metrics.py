#!/usr/bin/env python3
"""Collect local measurements and render validated server content once."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time

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


def task_budget(cfg):
    """Use the parent's actual allowance, leaving time to persist the sample."""
    seconds = numeric(os.environ.get('PISCHED_TASK_BUDGET_SEC', cfg['task']['timeout_sec']),
                      0, cfg['task']['timeout_sec'])
    return Budget(max(0, seconds - 2))


def collect_wifi(cfg, budget):
    """Retry transient command/association failures within one six-second cap."""
    wifi_budget = Budget(deadline=min(budget.deadline, time.monotonic() + 6))
    last = TimeoutError('Wi-Fi collection budget exhausted')
    for attempt in range(3):
        if wifi_budget.remaining() <= 1:
            break
        try:
            result = run_command(['iwconfig', cfg['network']['interface']],
                                 min(3, wifi_budget.remaining()))
            if result.returncode:
                raise RuntimeError(f'iwconfig failed (exit status {result.returncode})')
            return parse_wifi(result.output)
        except (OSError, RuntimeError, ValueError) as exc:
            last = exc
        if attempt < 2:
            wifi_budget.sleep(.25)
    raise last


def collect(cfg, event_id):
    data = {'schema_version': 2, 'event_id': event_id,
            'timestamp': datetime.now(timezone.utc).isoformat(), 'values': {}, 'failures': []}
    values = data['values']
    def fail(stage, exc):
        data['failures'].append({'stage': stage, 'message': str(exc)[:300]})
    try:
        budget = task_budget(cfg)
    except (ValueError, TypeError) as exc:
        fail('budget', exc)
        budget = Budget(0)
    # Measurements share a deadline before the render reservation. Extra retries
    # must not consume the screen's configured allowance, even on a short cycle.
    measurement_budget = Budget(deadline=budget.deadline - cfg['display']['timeout_sec'])
    client = PiSugarClient(cfg['pisugar'], measurement_budget)
    for field, key, low, high in [('battery_percent', 'battery', 0, 100),
                                  ('battery_voltage', 'battery_v', 0, 10),
                                  ('temperature', 'temperature', -40, 150)]:
        try:
            # Validate inside the client's one retry loop, rather than wrapping a
            # retrying get() in another loop and multiplying socket attempts.
            values[field] = client.get(key, validator=lambda value: numeric(value, low, high))
            if field == 'battery_percent':
                # Battery estimates need the successful reading's time, not the
                # beginning of a potentially long initialization/retry sequence.
                data['timestamp'] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            values[field] = None
            fail(key, exc)
    try:
        values['wifi_rssi'] = collect_wifi(cfg, measurement_budget)
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
        display_seconds = min(cfg['display']['timeout_sec'], budget.remaining())
        if display_seconds <= 1:
            raise TimeoutError('Display execution budget exhausted')
        result = run_command([cfg['display']['path']], display_seconds, env=env)
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
