"""Bounded device HTTP requests and a durable, bounded report outbox.

Each exchange runs in a short-lived interpreter.  A socket timeout alone cannot
bound a peer that slowly drips HTTP headers, so the parent enforces the complete
exchange deadline, including connection setup, headers and the response body.
"""
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


MAX_RESPONSE_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024
SERVER_DEFAULTS = {
    'base_url': 'http://192.168.76.71:8088',
    'device_id': 'epaper-pi-zero2',
    'token_file': '/etc/pisched/device.token',
    'timeout_sec': 5,
    'max_attempts': 3,
    'total_timeout_sec': 20,
    'backoff_sec': 1,
}


class ServerError(RuntimeError):
    """A sanitized failure; neither token nor response bodies are included."""
    def __init__(self, message, *, status_code=None, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def validate_server_config(raw):
    if not isinstance(raw, dict) or set(raw) - set(SERVER_DEFAULTS):
        raise ValueError('invalid server configuration keys')
    cfg = dict(SERVER_DEFAULTS, **raw)
    try:
        parsed = urlsplit(cfg['base_url'])
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError()
        ipaddress.ip_address(parsed.hostname)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        raise ValueError('server.base_url must be an HTTP(S) numeric IP origin') from None
    cfg['base_url'] = cfg['base_url'].rstrip('/')
    if (not isinstance(cfg['device_id'], str) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', cfg['device_id'])):
        raise ValueError('invalid server.device_id')
    token_file = cfg['token_file']
    if (not isinstance(token_file, str) or not Path(token_file).is_absolute() or
            '..' in Path(token_file).parts):
        raise ValueError('server.token_file must be an absolute path')
    for key, ceiling in (('timeout_sec', 5), ('total_timeout_sec', 20), ('backoff_sec', 5)):
        value = cfg[key]
        minimum = 0 if key == 'backoff_sec' else 0.001
        if (type(value) not in (int, float) or not math.isfinite(value) or
                not minimum <= value <= ceiling):
            raise ValueError(f'invalid server.{key}')
    if type(cfg['max_attempts']) is not int or not 1 <= cfg['max_attempts'] <= 3:
        raise ValueError('server.max_attempts must be between 1 and 3')
    return cfg


def validate_display(value):
    """Validate the fixed ASCII page independently from the remote debug flag."""
    if not isinstance(value, dict):
        raise ValueError('display must be an object')

    def text(field, limit, *, empty=False):
        if (not isinstance(field, str) or len(field) > limit or
                (not empty and not field) or
                any(not 32 <= ord(char) <= 126 for char in field)):
            raise ValueError('display contains invalid ASCII text or length')
        return field

    rows = value.get('rows')
    if not isinstance(rows, list) or len(rows) > 6:
        raise ValueError('display.rows must contain at most six rows')
    cleaned_rows = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('display row must be an object')
        cleaned_rows.append({'label': text(row.get('label'), 20),
                             'value': text(row.get('value'), 48, empty=True)})
    return {'title': text(value.get('title'), 40),
            'status': text(value.get('status'), 24), 'rows': cleaned_rows}


class _DeadlineBudget:
    def __init__(self, deadline, parent=None):
        self.deadline = deadline
        self.parent = parent

    def remaining(self):
        remaining = max(0.0, self.deadline - time.monotonic())
        return min(remaining, self.parent.remaining()) if self.parent is not None else remaining


class ServerClient:
    def __init__(self, cfg, budget):
        self.cfg = validate_server_config(cfg)
        self.budget = budget
        self.last_latency_ms = None

    def check_in(self, payload):
        value = self._request('/api/v1/check-in', payload)
        if not isinstance(value, dict) or type(value.get('debug')) is not bool:
            raise ServerError('server check-in must contain a boolean debug flag')
        # The caller adopts debug before validating content_version and display.
        return value

    def report(self, payload, budget=None):
        value = self._request('/api/v1/reports', payload, budget=budget)
        if not isinstance(value, dict) or value.get('accepted') is not True:
            raise ServerError('server did not acknowledge the report')
        return value

    def _request(self, path, payload, budget=None):
        deadline = _DeadlineBudget(time.monotonic() + self.cfg['total_timeout_sec'], self.budget)
        if budget is not None:
            deadline = _DeadlineBudget(time.monotonic() + min(deadline.remaining(), budget.remaining()),
                                       self.budget)
        if not isinstance(payload, dict):
            raise ValueError('server request payload must be an object')
        payload = dict(payload)
        if payload.setdefault('device_id', self.cfg['device_id']) != self.cfg['device_id']:
            raise ValueError('request device_id does not match configured device')
        try:
            body = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8')
        except (ValueError, TypeError):
            raise ValueError('server request must be JSON serializable') from None
        if len(body) > MAX_PAYLOAD_BYTES:
            raise ValueError('server request exceeds 256 KiB')
        try:
            with open(self.cfg['token_file'], 'r', encoding='ascii') as handle:
                token = handle.read(4097).strip()
        except (OSError, UnicodeError):
            raise ServerError('cannot read the device token file') from None
        if not token or len(token) > 4096 or any(not 33 <= ord(char) <= 126 for char in token):
            raise ServerError('device token file contains an invalid token')
        wire = json.dumps({'base_url': self.cfg['base_url'], 'path': path,
                           'token': token, 'payload': payload}, allow_nan=False).encode('utf-8')
        last_error = ServerError('server request deadline exhausted', retryable=True)
        for attempt in range(self.cfg['max_attempts']):
            allowed = min(self.cfg['timeout_sec'], deadline.remaining())
            if allowed <= 0:
                break
            start = time.monotonic()
            try:
                result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--exchange'],
                                        input=wire, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        timeout=allowed, check=False)
                if result.returncode:
                    raise ServerError('server exchange process failed', retryable=True)
                try:
                    result = json.loads(result.stdout)
                except (ValueError, UnicodeError):
                    raise ServerError('invalid server exchange result') from None
                if not isinstance(result, dict) or 'ok' not in result:
                    raise ServerError('invalid server exchange result')
                if result['ok'] is True:
                    return result['value']
                raise ServerError(result.get('error', 'server request failed'),
                                  status_code=result.get('status_code'),
                                  retryable=result.get('retryable') is True)
            except subprocess.TimeoutExpired:
                last_error = ServerError('server request timed out', retryable=True)
            except OSError:
                last_error = ServerError('cannot start server exchange process', retryable=True)
            except ServerError as exc:
                last_error = exc
            finally:
                self.last_latency_ms = round((time.monotonic() - start) * 1000, 1)
            if not last_error.retryable:
                raise last_error
            if attempt + 1 < self.cfg['max_attempts']:
                delay = min(self.cfg['backoff_sec'], deadline.remaining())
                if delay > 0:
                    time.sleep(delay)
        raise last_error


class ReportOutbox:
    """Single-process queue. The scheduler's cycle lock prevents concurrent writers."""
    def __init__(self, path, limit=100):
        self.path = Path(path)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('outbox limit must be between 1 and 100')
        self.limit = limit
        self.reports = []
        self.dropped_count = 0
        if self.path.exists():
            if self.path.stat().st_size > 101 * MAX_PAYLOAD_BYTES:
                raise ValueError('report outbox exceeds its size limit')
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                reports = value['reports']
                dropped = value['dropped_count']
                if (not isinstance(reports, list) or len(reports) > 100 or
                        any(not isinstance(report, dict) for report in reports) or
                        type(dropped) is not int or dropped < 0):
                    raise ValueError()
                self.reports = reports
                self.dropped_count = dropped
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise ValueError('invalid report outbox; existing file preserved') from None

    @property
    def pending_count(self):
        return len(self.reports)

    def enqueue(self, payload):
        encoded = json.dumps(payload, allow_nan=False)
        if not isinstance(payload, dict) or len(encoded.encode('utf-8')) > MAX_PAYLOAD_BYTES:
            raise ValueError('outbox report must be an object of at most 256 KiB')
        # Copy through JSON so later mutations of a cycle state cannot alter it.
        payload = json.loads(encoded)
        identity = self._identity(payload)
        if identity is not None:
            self.reports = [item for item in self.reports if self._identity(item) != identity]
        self.reports.append(payload)
        dropped = max(0, len(self.reports) - self.limit)
        if dropped:
            del self.reports[:dropped]
            self.dropped_count += dropped
        self._save()
        return dropped

    @staticmethod
    def _identity(payload):
        if not payload.get('event_id'):
            return None
        return (payload.get('device_id'), payload['event_id'],
                payload.get('phase', payload.get('report_phase', 'final')))

    def flush(self, client, budget):
        """Use one total retry budget for the entire backlog, not one per report."""
        shared = _DeadlineBudget(time.monotonic() + min(20, client.cfg['total_timeout_sec']), budget)
        sent = 0
        error = None
        while self.reports and shared.remaining() > 0:
            try:
                client.report(self.reports[0], budget=shared)
            except ServerError as exc:
                error = str(exc)
                break
            self.reports.pop(0)
            self._save()
            sent += 1
        result = {'sent': sent, 'pending': self.pending_count, 'dropped': self.dropped_count}
        if error:
            result['error'] = error
        return result

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.' + self.path.name, dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump({'reports': self.reports, 'dropped_count': self.dropped_count},
                          handle, allow_nan=False, separators=(',', ':'))
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _exchange(request):
    """Worker operation; its supervising process owns the authoritative deadline."""
    parsed = urlsplit(request['base_url'])
    connection_class = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=5)
    status = None
    try:
        body = json.dumps(request['payload'], allow_nan=False, separators=(',', ':')).encode('utf-8')
        connection.request('POST', request['path'], body=body,
                           headers={'Authorization': 'Bearer ' + request['token'],
                                    'Content-Type': 'application/json', 'Accept': 'application/json',
                                    'Connection': 'close'})
        response = connection.getresponse()
        status = response.status
        if not 200 <= status < 300:
            return {'ok': False, 'status_code': status, 'error': f'server HTTP {status}',
                    'retryable': 500 <= status < 600}
        length = response.getheader('Content-Length')
        if length is not None:
            try:
                if int(length) < 0 or int(length) > MAX_RESPONSE_BYTES:
                    raise ValueError()
            except ValueError:
                return {'ok': False, 'error': 'server response exceeds size limit or has invalid length'}
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            return {'ok': False, 'error': 'server response exceeds 64 KiB'}
        if length is not None and len(body) != int(length):
            return {'ok': False, 'error': 'server response was incomplete', 'retryable': True}
        try:
            value = json.loads(body, parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError):
            return {'ok': False, 'error': 'server returned invalid JSON'}
        return {'ok': True, 'value': value}
    except (OSError, http.client.HTTPException, ssl.SSLError, socket.timeout):
        return {'ok': False, 'status_code': status, 'error': 'server connection failed', 'retryable': True}
    finally:
        connection.close()


def _reject_json_constant(_value):
    raise ValueError('non-finite JSON number')


if __name__ == '__main__':
    if sys.argv[1:] != ['--exchange']:
        raise SystemExit(2)
    try:
        request = json.loads(sys.stdin.buffer.read(2 * MAX_PAYLOAD_BYTES))
        result = _exchange(request)
        sys.stdout.write(json.dumps(result, allow_nan=False))
    except Exception:
        # Untrusted network data must never turn traceback or credentials into output.
        sys.stdout.write(json.dumps({'ok': False, 'error': 'server exchange failed', 'retryable': True}))
