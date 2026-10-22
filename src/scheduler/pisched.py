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

DEBUG_PATHS = ('/boot/DEBUG', '/boot/firmware/DEBUG')
DEFAULTS = {
    'timezone': 'America/New_York',
    'state_file': '/var/lib/pisched/state.json',
    'metrics_file': '/var/lib/pisched/metrics.json',
    'log_file': '/var/log/pisched/pisched.log',
    'pisugar': {'uds_path': '/tmp/pisugar-server.sock', 'timeout_sec': 2, 'ready_timeout_sec': 15, 'max_attempts': 3, 'retry_delay_sec': 1},
    'task': {'command': '/usr/bin/python3 /opt/pisched/collect_metrics.py', 'timeout_sec': 120},
    'shutdown': {'command': '/sbin/shutdown -h now', 'shutdown_even_if_alarm_set_failed': True,
                 'max_attempts': 2, 'timeout_sec': 5},
    'network': {'target': '192.168.76.71', 'interface': 'wlan0', 'ping_count': 20,
                'ping_timeout_sec': 15},
    'display': {'path': '/home/wlt/IT8951/epd', 'timeout_sec': 45},
    'runtime': {'max_seconds': 240, 'time_sync_seconds': 30, 'shutdown_margin_seconds': 60,
                'low_battery_percent': 10, 'rtc_min_year': 2025},
    'notification': {'command': [], 'max_attempts': 3, 'timeout_sec': 5,
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
    schedule = raw.get('schedule')
    if (not isinstance(schedule, list) or not schedule or
            any(not isinstance(t, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in schedule)
            or len(set(schedule)) != len(schedule)):
        raise ValueError('schedule must contain distinct HH:MM times')
    if type(raw.get('repeat_mask', 127)) is not int or raw.get('repeat_mask', 127) != 127:
        raise ValueError('only daily schedules (repeat_mask=127) are supported')
    cfg.update(schedule=list(schedule), repeat_mask=127)
    if not isinstance(cfg['timezone'], str):
        raise ValueError('timezone must be a zone name')
    ZoneInfo(cfg['timezone'])
    for key in ('state_file', 'metrics_file', 'log_file'):
        if not isinstance(cfg[key], str) or not Path(cfg[key]).is_absolute() or '..' in Path(cfg[key]).parts:
            raise ValueError(f'{key} must be an absolute path')
    if len({cfg[k] for k in ('state_file', 'metrics_file', 'log_file')}) != 3:
        raise ValueError('state, metrics and log must use distinct paths')
    for section, key in [('pisugar', 'uds_path'), ('display', 'path')]:
        if not isinstance(cfg[section][key], str) or not Path(cfg[section][key]).is_absolute() or '..' in Path(cfg[section][key]).parts:
            raise ValueError(f'{section}.{key} must be an absolute path')
    for section in ('task', 'shutdown'):
        command = cfg[section]['command']
        if not isinstance(command, str) or not shlex.split(command) or not Path(shlex.split(command)[0]).is_absolute():
            raise ValueError(f'{section}.command must start with an absolute executable')
    for section in ('task', 'shutdown', 'display', 'notification', 'failure_display'):
        if cfg[section]['timeout_sec'] < 2:
            raise ValueError(f'{section}.timeout_sec must reserve process cleanup time (>=2)')
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
    for section in ('pisugar', 'task', 'shutdown', 'network', 'display', 'runtime', 'notification', 'failure_display'):
        for key, value in cfg[section].items():
            if key.endswith(('_sec', '_seconds')) or key in ('max_attempts', 'max_seconds', 'ping_count', 'rtc_min_year', 'low_battery_percent'):
                minimum = 0 if key in ('backoff_sec', 'low_battery_percent') else 1
                if type(value) is not int or not minimum <= value <= 3600:
                    raise ValueError(f'invalid positive integer: {section}.{key}')
    if cfg['notification']['max_attempts'] > 10 or cfg['shutdown']['max_attempts'] > 3 or cfg['pisugar']['max_attempts'] > 3:
        raise ValueError('too many retry attempts')
    if not 0 <= cfg['runtime']['low_battery_percent'] <= 100 or not 2020 <= cfg['runtime']['rtc_min_year'] <= 2099:
        raise ValueError('invalid battery threshold or RTC minimum year')
    for key in ('target', 'interface'):
        value = cfg['network'][key]
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', value) or value.startswith('-'):
            raise ValueError(f'invalid network.{key}')
    if cfg['runtime']['max_seconds'] < cleanup_reserve(cfg) + 5 or cfg['runtime']['max_seconds'] > 240:
        raise ValueError('runtime.max_seconds must leave cleanup time and be <=240 (systemd safety limit)')
    return cfg


def cleanup_reserve(cfg):
    return (cfg['notification']['total_timeout_sec'] + cfg['failure_display']['timeout_sec'] +
            cfg['shutdown']['max_attempts'] * cfg['shutdown']['timeout_sec'] + 3)


def load_config(path):
    return validate_config(json.loads(Path(path).read_text(encoding='utf-8')))


def compute_next_run(now, schedule):
    """Skip nonexistent wall times; run a repeated DST wall time only at fold=0."""
    if not schedule:
        raise ValueError('empty schedule')
    candidates = []
    for day in range(3):
        for value in schedule:
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
                raise ValueError(f'invalid HH:MM: {value}')
            hh, mm = map(int, value.split(':'))
            cand = (now + timedelta(days=day)).replace(hour=hh, minute=mm, second=0, microsecond=0, fold=0)
            if now.tzinfo is None:
                if cand > now:
                    candidates.append(cand)
            else:
                restored = datetime.fromtimestamp(cand.timestamp(), now.tzinfo)
                if (restored.replace(tzinfo=None) == cand.replace(tzinfo=None) and restored.fold == 0
                        and cand.timestamp() > now.timestamp()):
                    candidates.append(cand)
    return min(candidates, key=lambda d: d.timestamp() if d.tzinfo else d)


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

    def get(self, key):
        def exchange():
            response = self.cmd('get ' + key)
            prefix = key + ': '
            if not response.startswith(prefix):
                raise RuntimeError(f'invalid {key} response: {response[:200]}')
            value = response[len(prefix):]
            if 'error' in value.lower():
                raise RuntimeError(value[:200])
            return value
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
    while budget.remaining() > 0:
        try:
            if client.get('model') != 'PiSugar 3':
                raise RuntimeError('unexpected PiSugar model')
            rtc_datetime(client)
            client.budget = original
            return
        except (OSError, ValueError, RuntimeError) as exc:
            last = str(exc)
            budget.sleep(1)
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
    if not state['time_trusted']:
        raise RuntimeError('cannot confirm a wake alarm without trusted time')
    if abs((rtc_datetime(client) - datetime.now(timezone.utc)).total_seconds()) > 5:
        raise RuntimeError('RTC clock is not synchronized')
    now = datetime.now(ZoneInfo(cfg['timezone']))
    margin = timedelta(seconds=cfg['runtime']['shutdown_margin_seconds'])
    next_dt = compute_next_run(datetime.fromtimestamp((now + margin).timestamp(), now.tzinfo), cfg['schedule'])
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
            code = run_command(policy['command'], min(policy['timeout_sec'], end.remaining()), input_data=data).returncode
        except (OSError, ValueError, subprocess.SubprocessError):
            code = 125
        result.update(status='accepted' if code == 0 else 'failed', returncode=code)
        if code == 0:
            break
        if attempt + 1 < policy['max_attempts'] and end.remaining() > 1:
            end.sleep(min(policy['backoff_sec'] * (2 ** attempt), 5))
    return result


def request_shutdown(cfg, state, budget):
    if any(os.path.exists(path) for path in DEBUG_PATHS):
        return {'status': 'debug', 'attempts': 0}
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
    state = {'schema_version': 1, 'event_id': str(uuid.uuid4()), 'device': socket.gethostname(),
             'started_at': datetime.now(timezone.utc).isoformat(), 'time_trusted': False,
             'time_source': 'unknown', 'alarm_confirmed': False, 'next_run': None, 'failures': [],
             'notification': {'status': 'pending', 'attempts': 0}, 'shutdown': {'status': 'pending'},
             'last_trusted_time': previous.get('last_trusted_time')}
    log(f"Cycle started event={state['event_id']} budget={cfg['runtime']['max_seconds']}s", cfg['log_file'])
    def failure(stage, exc):
        state['failures'].append({'stage': stage, 'message': str(exc)[:500]})
        log(f'{stage} FAILED: {exc}', cfg['log_file'])
    def save():
        try:
            atomic_json(cfg['state_file'], state)
        except (OSError, ValueError) as exc:
            if not any(item['stage'] == 'state' for item in state['failures']):
                failure('state', exc)
    client = PiSugarClient(cfg['pisugar'], work, cfg['log_file'])
    try:
        if initial_error:
            raise ValueError(initial_error)
        ready = False
        try:
            wait_for_pisugar(client)
            ready = True
        except Exception as exc:
            failure('pisugar', exc)
        if ready:
            try:
                value = float(client.get('battery'))
                if not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError('invalid battery percentage')
                state['battery_percent'] = value
                state['low_battery'] = value <= cfg['runtime']['low_battery_percent']
                if state['low_battery']:
                    failure('battery', 'low battery; skipping task and notification retries')
            except Exception as exc:
                failure('battery', exc)
            try:
                prepare_time(cfg, client, state, previous, work)
            except Exception as exc:
                failure('time', exc)
        if not state.get('low_battery'):
            try:
                env = dict(os.environ, PISCHED_CONFIG=json.dumps(cfg), PISCHED_EVENT_ID=state['event_id'])
                rc = run_task(cfg['task']['command'], min(cfg['task']['timeout_sec'], work.remaining()), cfg['log_file'], env)
                log(f'Task rc={rc}', cfg['log_file'])
                if rc:
                    failure('task', f'exit status {rc}')
                metrics = read_json(cfg['metrics_file'])
                if metrics.get('event_id') == state['event_id']:
                    state['metrics'] = metrics
            except Exception as exc:
                failure('task', exc)
        try:
            program_alarm(cfg, client, state)
            log(f"Alarm confirmed: {state['next_run']}", cfg['log_file'])
        except Exception as exc:
            failure('alarm', exc)
    except Exception as exc:
        failure('cycle', exc)
    finally:
        state['finished_at'] = datetime.now(timezone.utc).isoformat()
        state['last_failure'] = previous.get('last_failure')
        save()
        try:
            # Leave fixed time for the display hook, persistence and shutdown.
            notify_budget = Budget(deadline=min(overall.deadline - cfg['failure_display']['timeout_sec'] -
                                                cfg['shutdown']['max_attempts'] * cfg['shutdown']['timeout_sec'] - 2,
                                                time.monotonic() + cfg['notification']['total_timeout_sec']))
            state['notification'] = notify_failure(cfg, state, notify_budget)
        except Exception as exc:
            state['notification'] = {'status': 'failed', 'attempts': 0}
            failure('notification', exc)
        save()
        try:
            if state['failures'] and cfg['failure_display']['command'] and not state.get('low_battery'):
                seconds = min(cfg['failure_display']['timeout_sec'], overall.remaining() -
                              cfg['shutdown']['max_attempts'] * cfg['shutdown']['timeout_sec'] - 2)
                code = run_command(cfg['failure_display']['command'], seconds,
                                   input_data=json.dumps(state).encode()).returncode
                state['failure_display'] = {'returncode': code}
        except Exception as exc:
            failure('failure_display', exc)
        if state['failures']:
            state['last_failure'] = {key: state[key] for key in ('event_id', 'started_at', 'failures', 'notification')}
        state['recovered'] = not state['failures'] and bool(state['last_failure'])
        state['shutdown'] = {'status': 'requesting'}
        save()
        try:
            state['shutdown'] = request_shutdown(cfg, state, overall)
        except Exception as exc:
            failure('shutdown', exc)
            state['shutdown'] = {'status': 'failed'}
        if state['shutdown']['status'] == 'failed':
            failure('shutdown', 'shutdown request failed; systemd stop hook will retry')
        state['elapsed_seconds'] = round(cfg['runtime']['max_seconds'] - overall.remaining(), 3)
        save()
        log(f"Cycle finished failures={len(state['failures'])} notification={state['notification']['status']} shutdown={state['shutdown']['status']} elapsed={state['elapsed_seconds']}s", cfg['log_file'])
    return 1 if state['failures'] else 0


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
