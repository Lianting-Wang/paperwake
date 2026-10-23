#!/usr/bin/env python3
"""One battery-powered refresh cycle, with bounded failure handling."""
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from server_client import ServerClient, ReportOutbox, validate_display, validate_server_config
from wake_plan import compute_next_run, validate_policy, validate_wake_plan

ALARM_RESERVE_SECONDS = 60
DEBUG_PATHS = ('/boot/DEBUG', '/boot/firmware/DEBUG')
DEFAULTS = {
    'timezone': 'America/New_York',
    'state_file': '/var/lib/pisched/state.json',
    'metrics_file': '/var/lib/pisched/metrics.json',
    'outbox_file': '/var/lib/pisched/outbox.json',
    'wake_plan_file': '/var/lib/pisched/wake-plan.json',
    'log_file': '/var/log/pisched/pisched.log',
    'pisugar': {'uds_path': '/tmp/pisugar-server.sock', 'timeout_sec': 2, 'ready_timeout_sec': 45,
                'max_attempts': 5, 'retry_delay_sec': 1, 'recovery_attempts': 5},
    'task': {'command': '/usr/bin/python3 /opt/pisched/collect_metrics.py', 'timeout_sec': 120},
    'shutdown': {'command': '/sbin/shutdown -h now', 'shutdown_even_if_alarm_set_failed': True,
                 'max_attempts': 3, 'timeout_sec': 5},
    'network': {'interface': 'wlan0'},
    'server': {'base_url': 'http://192.168.76.71:8088', 'device_id': 'epaper-pi-zero2',
               'token_file': '/etc/pisched/device.token', 'timeout_sec': 5, 'max_attempts': 5,
               'total_timeout_sec': 30, 'backoff_sec': 1},
    'display': {'path': '/home/wlt/IT8951/epd', 'timeout_sec': 45},
    'runtime': {'max_seconds': 240, 'time_sync_seconds': 30, 'shutdown_margin_seconds': 90,
                'low_battery_percent': 10, 'rtc_min_year': 2025},
    'notification': {'command': [], 'max_attempts': 5, 'timeout_sec': 5,
                     'total_timeout_sec': 20, 'backoff_sec': 1},
    'failure_display': {'command': [], 'timeout_sec': 10},
}


def rotate_log(path, max_bytes=1024*1024, backups=4):
    """Bound persistent logs without requiring a daemon during short boot cycles."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    for index in range(backups, 0, -1):
        source = Path(str(path) + f'.{index-1}') if index > 1 else path
        destination = Path(str(path) + f'.{index}')
        if source.exists():
            os.replace(source, destination)


def log(msg, log_file=None):
    line = f'[{datetime.now().isoformat(timespec="seconds")}] {msg}'
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass
    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            rotate_log(log_file)
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass  # State and notification still get a chance if logging fails.


def validate_config(raw):
    if not isinstance(raw, dict):
        raise ValueError('configuration must be an object')
    if set(raw) - set(DEFAULTS) - {'schedule', 'repeat_mask'}:
        raise ValueError('unknown configuration keys')
    cfg = {}
    for key, default in DEFAULTS.items():
        if isinstance(default, dict):
            value = raw.get(key, {})
            if not isinstance(value, dict) or set(value) - set(default):
                raise ValueError(f'invalid configuration section: {key}')
            cfg[key] = dict(default, **value)
        else:
            cfg[key] = raw.get(key, default)
    policy = validate_policy({'timezone': cfg['timezone'], 'schedule': raw.get('schedule')})
    if type(raw.get('repeat_mask', 127)) is not int or raw.get('repeat_mask', 127) != 127:
        raise ValueError('only daily schedules (repeat_mask=127) are supported')
    cfg.update(policy, repeat_mask=127)
    paths = ('state_file', 'metrics_file', 'outbox_file', 'wake_plan_file', 'log_file')
    for key in paths:
        if not isinstance(cfg[key], str) or not Path(cfg[key]).is_absolute() or '..' in Path(cfg[key]).parts:
            raise ValueError(f'{key} must be an absolute path')
    if len({cfg[k] for k in paths}) != len(paths):
        raise ValueError('state, metrics, outbox, wake plan and log must use distinct paths')
    for section, key in [('pisugar', 'uds_path'), ('display', 'path')]:
        if not isinstance(cfg[section][key], str) or not Path(cfg[section][key]).is_absolute() or '..' in Path(cfg[section][key]).parts:
            raise ValueError(f'{section}.{key} must be an absolute path')
    for section in ('task', 'shutdown'):
        command = cfg[section]['command']
        if not isinstance(command, str) or not shlex.split(command) or not Path(shlex.split(command)[0]).is_absolute():
            raise ValueError(f'{section}.command must start with an absolute executable')
    for section in ('notification', 'failure_display'):
        argv = cfg[section]['command']
        if not isinstance(argv, list) or any(not isinstance(x, str) or not x or '\x00' in x for x in argv):
            raise ValueError(f'{section}.command must be an argv array')
        if argv and not Path(argv[0]).is_absolute():
            raise ValueError(f'{section}.command executable must be absolute')
    if type(cfg['shutdown']['shutdown_even_if_alarm_set_failed']) is not bool:
        raise ValueError('shutdown_even_if_alarm_set_failed must be a JSON boolean')
    # The supported battery policy always shuts down; DEBUG is the explicit exception.
    if not cfg['shutdown']['shutdown_even_if_alarm_set_failed']:
        raise ValueError('battery operation requires shutdown_even_if_alarm_set_failed=true; use DEBUG for maintenance')
    for section in ('pisugar', 'task', 'shutdown', 'network', 'display', 'runtime', 'notification', 'failure_display', 'server'):
        for key, value in cfg[section].items():
            if key.endswith(('_sec', '_seconds')) or key in ('max_attempts', 'recovery_attempts', 'max_seconds', 'rtc_min_year', 'low_battery_percent'):
                minimum = 0 if key in ('backoff_sec', 'low_battery_percent') else 1
                if type(value) is not int or not minimum <= value <= 3600:
                    raise ValueError(f'invalid positive integer: {section}.{key}')
    for section in ('task', 'shutdown', 'display', 'notification', 'failure_display'):
        if cfg[section]['timeout_sec'] < 2:
            raise ValueError(f'{section}.timeout_sec must reserve process cleanup time (>=2)')
    if (cfg['notification']['max_attempts'] > 10 or cfg['shutdown']['max_attempts'] > 3
            or cfg['pisugar']['max_attempts'] > 5 or cfg['pisugar']['recovery_attempts'] > 5):
        raise ValueError('too many retry attempts')
    if not 0 <= cfg['runtime']['low_battery_percent'] <= 100 or not 2020 <= cfg['runtime']['rtc_min_year'] <= 2099:
        raise ValueError('invalid battery threshold or RTC minimum year')
    cfg['server'] = validate_server_config(cfg['server'])
    for key in ('interface',):
        value = cfg['network'][key]
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', value) or value.startswith('-'):
            raise ValueError(f'invalid network.{key}')
    if cfg['runtime']['max_seconds'] < cleanup_reserve(cfg) + 5 or cfg['runtime']['max_seconds'] > 240:
        raise ValueError('runtime.max_seconds must leave cleanup time and be <=240 (systemd safety limit)')
    if cfg['runtime']['shutdown_margin_seconds'] < cleanup_reserve(cfg):
        raise ValueError('runtime.shutdown_margin_seconds must cover server reporting, notification, display and shutdown cleanup')
    return cfg


def cleanup_reserve(cfg):
    return (cfg['server']['total_timeout_sec'] + cfg['notification']['total_timeout_sec'] + cfg['failure_display']['timeout_sec'] +
            cfg['shutdown']['max_attempts'] * cfg['shutdown']['timeout_sec'] + 3)


def load_config(path):
    return validate_config(json.loads(Path(path).read_text(encoding='utf-8')))


def iso8601_with_tz_ms(dt):
    return dt.astimezone().isoformat(timespec='milliseconds')


@dataclass
class Result:
    returncode: int
    output: str = ''


def run_command(argv, timeout_sec, input_data=None, env=None):
    """No pipe reads: the entire descendant group is reaped/terminated at a deadline."""
    if timeout_sec <= 1:
        return Result(124, 'No execution budget remaining')
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        try:
            try:
                proc.communicate(input=input_data, timeout=timeout_sec - 1)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                rc = 124
            finally:
                # Also clean up descendants left behind by an already-exited shell.
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=.4)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=.5)
            output.seek(0)
            return Result(rc, output.read(65536).decode('utf-8', errors='replace'))
        finally:
            if proc.stdin:
                proc.stdin.close()


def run_task(command, timeout_sec, log_file, env=None):
    result = run_command(shlex.split(command), timeout_sec, env=env)
    for line in result.output.splitlines():
        log('[task] ' + line, log_file)
    return result.returncode


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


class Budget:
    def __init__(self, seconds=None, deadline=None):
        self.deadline = time.monotonic() + seconds if deadline is None else deadline

    def remaining(self):
        return max(0, self.deadline - time.monotonic())

    def sleep(self, seconds):
        time.sleep(min(seconds, self.remaining()))


class PiSugarClient:
    def __init__(self, cfg, budget, log_file=None):
        self.cfg, self.budget, self.log_file = cfg, budget, log_file

    def cmd(self, command):
        end = min(self.budget.deadline, time.monotonic() + self.cfg['timeout_sec'])
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            def timeout():
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('PiSugar command deadline')
                sock.settimeout(remaining)
            timeout()
            sock.connect(self.cfg['uds_path'])
            timeout()
            sock.sendall((command.strip() + '\n').encode())
            data = b''
            while b'\n' not in data:
                timeout()
                block = sock.recv(1024)
                if not block:
                    raise RuntimeError('PiSugar closed an incomplete response')
                data += block
                if len(data) > 4096:
                    raise RuntimeError('PiSugar response too large')
        response = data.split(b'\n', 1)[0].decode().strip()
        return response

    def retry(self, operation, label):
        last = RuntimeError('PiSugar execution budget exhausted')
        for attempt in range(self.cfg['max_attempts']):
            if self.budget.remaining() <= 0:
                break
            try:
                return operation()
            except (OSError, ValueError, RuntimeError) as exc:
                last = exc
                log(f'[pisugar] {label} attempt {attempt+1} failed: {exc}', self.log_file)
                if attempt + 1 < self.cfg['max_attempts']:
                    self.budget.sleep(self.cfg['retry_delay_sec'])
        raise last

    def get(self, key, validator=None):
        def exchange():
            response = self.cmd('get ' + key)
            prefix = key + ': '
            if not response.startswith(prefix):
                raise RuntimeError(f'invalid {key} response: {response[:200]}')
            value = response[len(prefix):]
            if any(text in value.lower() for text in ('error', 'not connected', 'not ready')):
                raise RuntimeError(value[:200])
            return validator(value) if validator is not None else value
        return self.retry(exchange, 'get ' + key)

    def done(self, command):
        def exchange():
            response = self.cmd(command)
            if response != command.split()[0] + ': done':
                raise RuntimeError(response[:200])
        return self.retry(exchange, command.split()[0])


def time_synchronized(timeout_sec=3):
    if timeout_sec <= 1:
        return False
    try:
        result = run_command(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], timeout_sec)
        return result.returncode == 0 and result.output.strip() == 'yes'
    except OSError:
        return False


def wait_for_time_sync(log_file, timeout_sec=30):
    budget = Budget(timeout_sec)
    while budget.remaining() > 1:
        if time_synchronized(min(3, budget.remaining())):
            log('System time synchronized.', log_file)
            return True
        budget.sleep(2)
    return False


def rtc_datetime(client):
    dt = datetime.fromisoformat(client.get('rtc_time'))
    if dt.tzinfo is None:
        raise ValueError('RTC time has no UTC offset')
    return dt


def wait_for_pisugar(client):
    budget = Budget(deadline=min(client.budget.deadline, time.monotonic() + client.cfg['ready_timeout_sec']))
    last = 'not ready'
    original = client.budget
    client.budget = budget
    try:
        while budget.remaining() > 0:
            try:
                if client.get('model') != 'PiSugar 3':
                    raise RuntimeError('unexpected PiSugar model')
                rtc_datetime(client)
                return
            except (OSError, ValueError, RuntimeError) as exc:
                last = str(exc)
                budget.sleep(1)
    finally:
        client.budget = original
    raise RuntimeError('PiSugar not ready: ' + last)


def prepare_time(cfg, client, state, previous, budget):
    synced = wait_for_time_sync(cfg['log_file'], min(cfg['runtime']['time_sync_seconds'], budget.remaining()))
    if synced:
        state.update(time_trusted=True, time_source='ntp')
        state['last_trusted_time'] = datetime.now(timezone.utc).isoformat()
        client.done('rtc_pi2rtc')
        if abs((rtc_datetime(client) - datetime.now(timezone.utc)).total_seconds()) > 5:
            raise RuntimeError('RTC write/readback differs from synchronized time')
        return
    rtc = rtc_datetime(client)
    floor = datetime(cfg['runtime']['rtc_min_year'], 1, 1, tzinfo=timezone.utc)
    if previous.get('last_trusted_time'):
        prior = datetime.fromisoformat(previous['last_trusted_time'])
        if prior.tzinfo is None:
            raise RuntimeError('saved trusted time lacks a UTC offset')
        floor = max(floor, prior - timedelta(minutes=5))
    if not floor <= rtc < datetime(2100, 1, 1, tzinfo=timezone.utc):
        raise RuntimeError('RTC outside trusted range; refusing to overwrite system clock')
    # A synchronization that completed at the boundary always takes priority over RTC.
    if time_synchronized(min(3, budget.remaining())):
        state.update(time_trusted=True, time_source='ntp')
        state['last_trusted_time'] = datetime.now(timezone.utc).isoformat()
        client.done('rtc_pi2rtc')
    else:
        client.done('rtc_rtc2pi')
        state.update(time_trusted=True, time_source='rtc')
    if abs((rtc_datetime(client) - datetime.now(timezone.utc)).total_seconds()) > 5:
        raise RuntimeError('RTC and system clock disagree after synchronization')


def program_alarm(cfg, client, state):
    state['alarm_confirmed'] = False
    if not state['time_trusted']:
        raise RuntimeError('cannot confirm a wake alarm without trusted time')
    if abs((rtc_datetime(client) - datetime.now(timezone.utc)).total_seconds()) > 5:
        raise RuntimeError('RTC clock is not synchronized')
    now = datetime.now(ZoneInfo(cfg['timezone']))
    margin = timedelta(seconds=cfg['runtime']['shutdown_margin_seconds'])
    # ZoneInfo wall-clock addition can cross a repeated hour or reset fold=1.
    # The shutdown margin is elapsed time, independent of DST clock changes.
    earliest = datetime.fromtimestamp(now.timestamp() + margin.total_seconds(), now.tzinfo)
    # The daily policy remains authoritative against the device's trusted clock.
    # A server clock jump or an expired absolute list must never skip valid days.
    next_dt = compute_next_run(earliest, cfg['schedule'])
    state['next_run'] = next_dt.isoformat()
    # A daily UTC alarm can ring a day early across a 25h DST interval, or when
    # skipping a nonexistent wall time. Restrict only those long spans to the
    # target UTC weekday (PiSugar: Sunday is bit 0), then recompute next boot.
    repeat = 127
    if next_dt.timestamp() - now.timestamp() >= 24 * 3600:
        repeat = 1 << ((next_dt.astimezone(timezone.utc).weekday() + 1) % 7)
    state['alarm_repeat'] = repeat
    client.done(f'rtc_alarm_set {next_dt.isoformat(timespec="milliseconds")} {repeat}')
    alarm = datetime.fromisoformat(client.get('rtc_alarm_time'))
    if alarm.tzinfo is None or alarm.astimezone(timezone.utc).time() != next_dt.astimezone(timezone.utc).time():
        raise RuntimeError('alarm time readback mismatch (UTC time of day)')
    if client.get('alarm_repeat') != str(repeat) or client.get('rtc_alarm_enabled') != 'true':
        raise RuntimeError('alarm repeat/enabled readback mismatch')
    if next_dt.timestamp() - datetime.now(timezone.utc).timestamp() < margin.total_seconds():
        raise RuntimeError('alarm no longer leaves the required shutdown margin')
    state['alarm_confirmed'] = True


def recover_alarm(cfg, client, state, previous, budget, prepared=False):
    """Retry the complete RTC transaction, sharing the existing work deadline."""
    last = RuntimeError('PiSugar recovery budget exhausted')
    result = state['pisugar_recovery'] = {'status': 'budget_exhausted', 'attempts': 0, 'errors': []}
    for attempt in range(cfg['pisugar']['recovery_attempts']):
        if budget.remaining() <= 0:
            break
        result['attempts'] += 1
        try:
            # A partially written alarm must be preceded by a new readiness and
            # clock check. Never bypass this by merely declaring time trusted.
            if not prepared or attempt:
                wait_for_pisugar(client)
                prepare_time(cfg, client, state, previous, budget)
            program_alarm(cfg, client, state)
            result['status'] = 'confirmed'
            return
        except (OSError, ValueError, RuntimeError) as exc:
            last = exc
            result['errors'].append(str(exc)[:500])
            result['status'] = 'failed'
            log(f'[pisugar] recovery {attempt+1} failed: {exc}', cfg['log_file'])
            if attempt + 1 < cfg['pisugar']['recovery_attempts']:
                budget.sleep(cfg['pisugar']['retry_delay_sec'])
    if budget.remaining() <= 0:
        result['status'] = 'budget_exhausted'
    raise RuntimeError('cannot confirm wake alarm after recovery: ' + str(last))


def load_wake_plan(path):
    """Missing cache is expected; a damaged cache must be reported explicitly."""
    path = Path(path)
    if not path.exists():
        return None
    if path.stat().st_size > 131072:
        raise ValueError('wake plan cache exceeds its size limit')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except RecursionError as exc:
        raise ValueError('wake plan cache is nested too deeply') from exc
    return validate_wake_plan(value)


def wake_plan_summary(cfg, plan, source, state, cached=False):
    policy = plan or cfg
    summary = {'source': source, 'version': plan['version'] if plan else None,
               'timezone': policy['timezone'], 'schedule': policy['schedule'],
               'generated_at': plan['generated_at'] if plan else None,
               'valid_until': plan['occurrences'][-1] if plan else None,
               'occurrence_count': len(plan['occurrences']) if plan else 0,
               'future_occurrences': [], 'cached': cached}
    if state.get('time_trusted'):
        current = datetime.now(timezone.utc)
        cursor = datetime.fromtimestamp(current.timestamp() + cfg['runtime']['shutdown_margin_seconds'],
                                        ZoneInfo(policy['timezone']))
        for _ in range(5):
            cursor = compute_next_run(cursor, policy['schedule'])
            summary['future_occurrences'].append(cursor.astimezone(timezone.utc).isoformat(timespec='seconds'))
        summary['continued_from_policy'] = bool(plan and any(
            item not in plan['occurrences'] for item in summary['future_occurrences']))
    return summary


def notify_failure(cfg, state, budget):
    policy = cfg['notification']
    result = {'status': 'not_configured', 'attempts': 0}
    if not state['failures']:
        return {'status': 'not_needed', 'attempts': 0}
    if not policy['command']:
        return result
    result['status'] = 'budget_exhausted'
    end = Budget(deadline=min(budget.deadline, time.monotonic() + policy['total_timeout_sec']))
    for attempt in range(policy['max_attempts']):
        if state.get('low_battery') or end.remaining() <= 1:
            break
        result['attempts'] += 1
        try:
            data = json.dumps(state, allow_nan=False).encode()
            # Divide a short total window across the attempts it can still fit;
            # each process needs time for both execution and group cleanup.
            available_attempts = min(policy['max_attempts'] - attempt, max(1, int(end.remaining() // 2)))
            seconds = min(policy['timeout_sec'], end.remaining() / available_attempts)
            code = run_command(policy['command'], seconds, input_data=data).returncode
        except (OSError, ValueError, subprocess.SubprocessError):
            code = 125
        result.update(status='accepted' if code == 0 else 'failed', returncode=code)
        if code == 0:
            break
        if attempt + 1 < policy['max_attempts'] and end.remaining() > 1:
            remaining_attempts = min(policy['max_attempts'] - attempt - 1, int(end.remaining() // 2))
            spare = max(0, end.remaining() - 2 * remaining_attempts)
            end.sleep(min(policy['backoff_sec'] * (2 ** attempt), 5, spare))
    return result


def request_shutdown(cfg, state, budget):
    if any(os.path.exists(path) for path in DEBUG_PATHS):
        return {'status': 'debug', 'attempts': 0}
    if state.get('remote_debug') is True:
        return {'status': 'debug', 'source': 'remote', 'attempts': 0}
    result = {'status': 'failed', 'attempts': 0}
    for _ in range(cfg['shutdown']['max_attempts']):
        remaining = budget.remaining()
        if remaining <= 1:
            break
        result['attempts'] += 1
        try:
            code = run_command(shlex.split(cfg['shutdown']['command']), min(cfg['shutdown']['timeout_sec'], remaining)).returncode
        except (OSError, subprocess.SubprocessError):
            code = 125
        result['returncode'] = code
        if code == 0:
            result['status'] = 'requested'
            break
    return result


def run_cycle(cfg, initial_error=None):
    """The testable cycle; callers must replace device effects for offline tests."""
    overall = Budget(cfg['runtime']['max_seconds'])
    work = Budget(deadline=overall.deadline - cleanup_reserve(cfg))
    previous = read_json(cfg['state_file'])
    state = {'schema_version': 2, 'device_id': cfg['server']['device_id'], 'event_id': str(uuid.uuid4()), 'device': socket.gethostname(),
             'started_at': datetime.now(timezone.utc).isoformat(), 'time_trusted': False,
             'time_source': 'unknown', 'alarm_confirmed': False, 'next_run': None, 'failures': [],
             'notification': {'status': 'pending', 'attempts': 0}, 'shutdown': {'status': 'pending'},
             'last_trusted_time': previous.get('last_trusted_time'), 'remote_debug': False,
             'server': {'status': 'pending'}, 'display': {'status': 'skipped'}}
    plan, plan_source, plan_cached = None, 'local', False
    log(f"Cycle started event={state['event_id']} budget={cfg['runtime']['max_seconds']}s", cfg['log_file'])
    def failure(stage, exc):
        state['failures'].append({'stage': stage, 'message': str(exc)[:500]})
        log(f'{stage} FAILED: {exc}', cfg['log_file'])
    def refresh_summary():
        if state['failures']:
            state['last_failure'] = {key: state[key] for key in ('event_id', 'started_at', 'failures', 'notification')}
        state['recovered'] = not state['failures'] and bool(state.get('last_failure'))
    def refresh_timing():
        finished = datetime.now(timezone.utc)
        elapsed = time.monotonic() - (overall.deadline - cfg['runtime']['max_seconds'])
        state['finished_at'] = finished.isoformat()
        state['elapsed_seconds'] = round(elapsed, 3)
        if state['time_trusted']:
            # Early boot wall time may be stale until NTP/RTC corrects it.
            state['started_at'] = (finished - timedelta(seconds=elapsed)).isoformat()
        state['wake_plan'] = wake_plan_summary(cfg, plan, plan_source, state, plan_cached)
    def save():
        refresh_summary()
        try:
            atomic_json(cfg['state_file'], state)
        except (OSError, ValueError) as exc:
            if not any(item['stage'] == 'state' for item in state['failures']):
                failure('state', exc)
            refresh_summary()
    def queue_report(phase):
        replacing = any(item.get('event_id') == state['event_id'] and item.get('phase') == phase
                        for item in outbox.reports)
        state['outbox_dropped'] = outbox.dropped_count + int(
            outbox.pending_count >= outbox.limit and not replacing)
        outbox.enqueue(dict(state, phase=phase))
        state['outbox_dropped'] = outbox.dropped_count

    client = PiSugarClient(cfg['pisugar'], work, cfg['log_file'])
    server = ServerClient(cfg['server'], work)
    outbox = None
    try:
        outbox = ReportOutbox(cfg['outbox_file'])
    except Exception as exc:
        failure('outbox', exc)
    try:
        plan = load_wake_plan(cfg['wake_plan_file'])
        if plan:
            plan_source, plan_cached = 'cache', True
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        failure('wake_plan_cache', exc)
    try:
        if initial_error:
            raise ValueError(initial_error)
        ready, prepared = False, False
        # Initial boot probing must leave time for check-in and a second RTC
        # recovery phase, even if the first readiness window is exhausted.
        startup = Budget(deadline=work.deadline - ALARM_RESERVE_SECONDS - cfg['server']['total_timeout_sec'])
        client.budget = startup
        try:
            try:
                wait_for_pisugar(client)
                ready = True
            except Exception as exc:
                failure('pisugar', exc)
            if ready:
                try:
                    def battery(value):
                        number = float(value)
                        if not math.isfinite(number) or not 0 <= number <= 100:
                            raise ValueError('invalid battery percentage')
                        return number
                    value = float(client.get('battery', validator=battery))
                    state['battery_percent'] = value
                    state['low_battery'] = value <= cfg['runtime']['low_battery_percent']
                    if state['low_battery']:
                        failure('battery', 'low battery; skipping task and notification retries')
                except Exception as exc:
                    failure('battery', exc)
                try:
                    prepare_time(cfg, client, state, previous, startup)
                    prepared = True
                except Exception as exc:
                    failure('time', exc)
        finally:
            client.budget = work
        if not state.get('low_battery'):
            page = None
            try:
                refresh_timing()
                reply = server.check_in({
                    'device_id': state['device_id'], 'event_id': state['event_id'],
                    'started_at': state['started_at'], 'time_trusted': state['time_trusted'],
                    'battery_percent': state.get('battery_percent'),
                    'local_debug': any(os.path.exists(path) for path in DEBUG_PATHS),
                    'wake_schedule': {'timezone': cfg['timezone'], 'schedule': cfg['schedule']},
                    'previous': {key: previous.get(key) for key in
                                 ('event_id', 'finished_at', 'shutdown', 'failures')},
                })
                # Adopt this only after authenticated, well-formed check-in; never load from disk.
                state['remote_debug'] = reply['debug']
                state['server'] = {'status': 'connected', 'latency_ms': server.last_latency_ms}
                # Wake policy and display have independent validation. Neither
                # can invalidate the current authenticated maintenance flag.
                if 'wake_plan' in reply:
                    try:
                        downloaded = validate_wake_plan(reply['wake_plan'])
                        plan, plan_source, plan_cached = downloaded, 'server', False
                        try:
                            atomic_json(cfg['wake_plan_file'], downloaded)
                            plan_cached = True
                        except (OSError, ValueError) as exc:
                            failure('wake_plan_cache', exc)
                    except (ValueError, TypeError, OverflowError) as exc:
                        failure('wake_plan', exc)
                version = reply.get('content_version')
                if type(version) is not int or version < 1:
                    raise ValueError('invalid content version')
                state['content_version'] = version
                page = validate_display(reply.get('display'))
            except Exception as exc:
                failure('server_check_in', exc)
                if state['server']['status'] == 'pending':
                    state['server']['status'] = 'failed'
            if page is not None:
                try:
                    task_seconds = min(cfg['task']['timeout_sec'], max(0, work.remaining() - ALARM_RESERVE_SECONDS))
                    env = dict(os.environ, PISCHED_CONFIG=json.dumps(cfg), PISCHED_EVENT_ID=state['event_id'],
                               PISCHED_PAGE=json.dumps(page), PISCHED_HTTP_LATENCY_MS=str(server.last_latency_ms),
                               PISCHED_TASK_BUDGET_SEC=str(task_seconds))
                    rc = run_task(cfg['task']['command'], task_seconds, cfg['log_file'], env)
                    log(f'Task rc={rc}', cfg['log_file'])
                    if rc:
                        failure('task', f'exit status {rc}')
                    metrics = read_json(cfg['metrics_file'])
                    if metrics.get('event_id') == state['event_id']:
                        state['metrics'] = metrics
                        state['display'] = {'status': 'ok' if metrics.get('display_returncode') == 0 else 'failed'}
                except Exception as exc:
                    failure('task', exc)
        try:
            alarm_cfg = dict(cfg, timezone=plan['timezone'], schedule=plan['schedule']) if plan else cfg
            recover_alarm(alarm_cfg, client, state, previous, work, prepared)
            log(f"Alarm confirmed: {state['next_run']}", cfg['log_file'])
        except Exception as exc:
            failure('alarm', exc)
    except Exception as exc:
        failure('cycle', exc)
    finally:
        refresh_timing()
        state['last_failure'] = previous.get('last_failure')
        save()
        try:
            # Leave fixed time for the display hook, persistence and shutdown.
            notify_budget = Budget(deadline=min(overall.deadline - cfg['server']['total_timeout_sec'] - cfg['failure_display']['timeout_sec'] -
                                                cfg['shutdown']['max_attempts'] * cfg['shutdown']['timeout_sec'] - 2,
                                                time.monotonic() + cfg['notification']['total_timeout_sec']))
            state['notification'] = notify_failure(cfg, state, notify_budget)
        except Exception as exc:
            state['notification'] = {'status': 'failed', 'attempts': 0}
            failure('notification', exc)
        save()
        try:
            if (state['failures'] and cfg['failure_display']['command'] and not state.get('low_battery')
                    and state['server']['status'] == 'connected'):
                seconds = min(cfg['failure_display']['timeout_sec'], overall.remaining() - cfg['server']['total_timeout_sec'] -
                              cfg['shutdown']['max_attempts'] * cfg['shutdown']['timeout_sec'] - 2)
                code = run_command(cfg['failure_display']['command'], seconds,
                                   input_data=json.dumps(state).encode()).returncode
                state['failure_display'] = {'returncode': code}
        except Exception as exc:
            failure('failure_display', exc)
        local_debug = any(os.path.exists(path) for path in DEBUG_PATHS)
        state['debug_hold'] = local_debug or state['remote_debug']
        state['shutdown_intent'] = 'hold' if state['debug_hold'] else 'poweroff'
        state['shutdown'] = ({'status': 'debug', 'source': 'local' if local_debug else 'remote'}
                             if state['debug_hold'] else {'status': 'requesting'})
        refresh_timing()
        save()
        report_budget = Budget(deadline=min(time.monotonic() + cfg['server']['total_timeout_sec'],
            overall.deadline - cfg['shutdown']['max_attempts'] * cfg['shutdown']['timeout_sec'] - 2))
        queued = False
        if outbox is not None:
            try:
                queue_report('final')
                queued = True
                # One shared reporting budget covers the whole backlog, never per queued item.
                if not state.get('low_battery') and state['server']['status'] == 'connected':
                    reporter = ServerClient(cfg['server'], report_budget)
                    state['reporting'] = outbox.flush(reporter, report_budget)
                    if state['reporting'].get('error') or state['reporting'].get('pending'):
                        failure('server_report', state['reporting'].get('error', 'reporting deadline exhausted'))
                else:
                    state['reporting'] = {'status': 'queued'}
            except Exception as exc:
                failure('server_report' if queued else 'outbox', exc)
            if any(item['stage'] == 'server_report' for item in state['failures']):
                try:
                    # An ACK may have been lost after acceptance. A distinct outcome
                    # supplements the immutable final report on the next connection.
                    refresh_timing()
                    refresh_summary()
                    queue_report('outcome')
                except Exception as exc:
                    failure('outbox', exc)
        if not queued and not state.get('low_battery') and state['server']['status'] == 'connected':
            # A damaged/unwritable queue must not suppress live reporting of that fault.
            try:
                ServerClient(cfg['server'], report_budget).report(dict(state, phase='final'))
            except Exception as exc:
                failure('server_report', exc)
        save()
        try:
            state['shutdown'] = request_shutdown(cfg, state, overall)
        except Exception as exc:
            failure('shutdown', exc)
            state['shutdown'] = {'status': 'failed'}
        if state['shutdown']['status'] == 'failed':
            failure('shutdown', 'shutdown request failed; systemd stop hook will retry')
            first_notification = state['notification']['status'] == 'not_needed'
            if first_notification:
                state['notification'] = {'status': 'pending', 'attempts': 0}
            save()
            if first_notification:
                # This is the first failure, discovered after the original
                # notification phase. Reuse its deadline, never a new budget.
                try:
                    state['notification'] = notify_failure(cfg, state, notify_budget)
                except Exception as exc:
                    state['notification'] = {'status': 'failed', 'attempts': 0}
                    failure('notification', exc)
        refresh_timing()
        save()
        if state['shutdown']['status'] == 'failed' and outbox is not None:
            try:
                queue_report('outcome')
            except Exception as exc:
                failure('outbox', exc)
                save()
        log(f"Cycle finished failures={len(state['failures'])} notification={state['notification']['status']} shutdown={state['shutdown']['status']} elapsed={state['elapsed_seconds']}s", cfg['log_file'])
    # Exit 10 means a completed maintenance cycle with recorded failures.
    # systemd accepts it; signals and timeouts still trigger the independent backstop.
    return (10 if state['remote_debug'] else 1) if state['failures'] else 0


def main():
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} /etc/pisched/config.json')
        return 2
    # A bad config must also reach the battery shutdown policy, using safe defaults.
    error = None
    try:
        cfg = load_config(sys.argv[1])
    except Exception as exc:
        cfg = validate_config({'schedule': ['08:00']})
        error = 'configuration invalid: ' + str(exc)
    with open('/run/pisched.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('Another cycle is already running.', cfg['log_file'])
            return 0
        return run_cycle(cfg, error)


if __name__ == '__main__':
    sys.exit(main())
