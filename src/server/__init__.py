"""Authenticated device check-ins and a small persistent e-paper control panel."""
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash

from src.scheduler.wake_plan import make_wake_plan, validate_policy

from .i18n import LANGUAGE_COOKIE, LANGUAGES, get_language, t
from .history import build_history_data, cycle_has_failures, merge_cycle
from .battery import estimate_battery
from .preview import DEFAULT_LIBRARY, PreviewUnavailable, get_renderer

DEFAULT_DISPLAY = {'title': 'E-PAPER', 'status': 'Ready', 'rows': [
    {'label': 'Device', 'value': 'Connected'}, {'label': 'Content', 'value': 'Edit in the control panel'}]}
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')


UI_LABELS = {
    'check-in': '设备报到', 'final': '最终报告', 'outcome': '补充结果',
    'hold': '保持开机', 'poweroff': '准备关机',
    'task': '采集任务', 'display': '屏幕刷新', 'pisugar': '电源管理',
    'time': '时钟同步', 'alarm': '唤醒闹钟', 'battery': '电量',
    'server_check_in': '服务器连接', 'server_report': '报告上传',
    'notification': '异常通知', 'outbox': '报告队列', 'shutdown': '关机',
    'state': '状态保存', 'failure_display': '异常画面', 'cycle': '运行周期',
    'ok': '成功', 'success': '成功', 'failed': '失败', 'skipped': '已跳过',
    'pending': '等待处理', 'requesting': '正在请求', 'requested': '已请求',
    'debug': '调试留机',
}
HTTP_ERRORS = {
    400: ('请求无效', '请检查填写的内容或链接，然后重试。'),
    401: ('需要身份验证', '请登录后重试。'),
    403: ('无权访问', '当前账号没有访问此页面的权限。'),
    404: ('页面不存在', '未找到此页面或设备，请返回概览。'),
    405: ('不支持此操作', '请通过页面中的按钮提交操作。'),
    413: ('提交内容过大', '请减少提交内容后重试。'),
    500: ('服务器错误', '服务暂时无法处理请求，请稍后重试。'),
}


def ui_label(value):
    return t(UI_LABELS.get(value, value)) if isinstance(value, str) else t('未知')


def display_result_label(payload):
    value = payload.get('display', {}).get('status', payload.get('display_result',
        payload.get('task', {}).get('status', payload.get('metrics', {}).get('display_returncode'))))
    if type(value) is int:
        return t('成功') if value == 0 else t('失败（退出码 {code}）', code=value)
    return ui_label(value)


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    """Return an aware UTC datetime, or None for an untrusted/missing timestamp."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, OverflowError):
        return None


def local_day_start(day, zone):
    """Find the first real instant of a calendar date in an IANA timezone."""
    candidates = [day.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc) for fold in (0, 1)]
    midnights = [value for value in candidates
                 if value.astimezone(zone).replace(tzinfo=None) == day]
    if midnights:
        return min(midnights)
    # Some zones skip midnight. Search across the transition, including half-hour
    # jumps, instead of assuming that the date always starts at 00:00 or 01:00.
    low, high = sorted(candidates)
    if high.astimezone(zone).date() != day.date():
        raise ValueError('The timezone skips this calendar date.')
    while high - low > timedelta(microseconds=1):
        middle = low + (high - low) / 2
        if middle.astimezone(zone).date() < day.date():
            low = middle
        else:
            high = middle
    return high


def validate_display(value):
    if not isinstance(value, dict) or set(value) != {'title', 'status', 'rows'}:
        raise ValueError(t('屏幕内容必须包含标题、状态和信息行。'))
    def text(key, raw, limit, allow_empty=False):
        if not isinstance(raw, str) or len(raw) > limit or (not raw and not allow_empty):
            raise ValueError(t('{field}须包含 {minimum}–{maximum} 个字符。',
                               field=t(key), minimum=0 if allow_empty else 1, maximum=limit))
        if any(ord(char) < 32 or ord(char) > 126 for char in raw):
            raise ValueError(t('{field}仅支持英文、数字和可打印 ASCII 符号。', field=t(key)))
        return raw
    rows = value['rows']
    if not isinstance(rows, list) or len(rows) > 6:
        raise ValueError(t('最多可设置六条显示信息。'))
    result = {'title': text('标题', value['title'], 40),
              'status': text('状态', value['status'], 24), 'rows': []}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'label', 'value'}:
            raise ValueError(t('每条显示信息都需要名称和内容。'))
        result['rows'].append({'label': text('名称', row['label'], 20),
                               'value': text('内容', row['value'], 48, True)})
    return result


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(_database(), timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db


def _database():
    from flask import current_app
    return current_app.config['DATABASE']


def create_app(config=None):
    app = Flask(__name__)
    app.jinja_env.filters.update(ui_label=ui_label, display_result_label=display_result_label)
    app.jinja_env.globals['t'] = t
    app.jinja_env.globals['style_version'] = hashlib.sha256(
        (Path(__file__).parent / 'static/style.css').read_bytes()).hexdigest()[:12]
    def asset_version(name):
        return hashlib.sha256((Path(__file__).parent / 'static' / name).read_bytes()).hexdigest()[:12]
    app.jinja_env.globals['asset_version'] = asset_version

    @app.context_processor
    def language_context():
        tabs = {'dashboard': 'overview', 'settings': 'content', 'wake_settings': 'wake',
                'history': 'history', 'history_manage': 'history'}
        return {'ui_language': get_language(), 'active_tab': tabs.get(request.endpoint),
                'navigation_device_id': (request.view_args or {}).get('device_id', app.config['DEVICE_ID'])}
    app.config.from_mapping(
        DATABASE=os.environ.get('DATABASE', 'data/runtime/server.sqlite3'),
        SECRET_KEY=os.environ.get('SECRET_KEY', ''),
        ADMIN_USERNAME=os.environ.get('ADMIN_USERNAME', 'admin'),
        ADMIN_PASSWORD_HASH=os.environ.get('ADMIN_PASSWORD_HASH', ''),
        DEVICE_ID=os.environ.get('DEVICE_ID', 'epaper-pi-zero2'),
        DEVICE_TOKEN=os.environ.get('DEVICE_TOKEN', ''),
        BASE_URL=os.environ.get('BASE_URL', 'http://192.168.76.109:8088'),
        NOW=utc_now,
        PAGE_RENDERER_LIBRARY=os.environ.get('PAGE_RENDERER_LIBRARY', str(DEFAULT_LIBRARY)),
        PAGE_PREVIEW_WIDTH=1872,
        MAX_CONTENT_LENGTH=262144, MAX_FORM_MEMORY_SIZE=65536,
        SESSION_COOKIE_NAME='epaper_admin', SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax', PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if config:
        app.config.update(config)
    for name in ('SECRET_KEY', 'DEVICE_TOKEN'):
        if not isinstance(app.config[name], str) or len(app.config[name]) < 32:
            raise ValueError(f'{name} must be a random secret of at least 32 characters.')
    if not app.config['ADMIN_PASSWORD_HASH'].startswith(('scrypt:', 'pbkdf2:')):
        raise ValueError('ADMIN_PASSWORD_HASH must be a Werkzeug password hash; use python -m src.server hash-password.')
    if not IDENTIFIER.fullmatch(app.config['DEVICE_ID']):
        raise ValueError('DEVICE_ID is not a valid identifier.')
    if not app.config['BASE_URL'].startswith(('https://', 'http://')):
        raise ValueError('BASE_URL must start with http:// or https://.')
    app.config['SESSION_COOKIE_SECURE'] = app.config['BASE_URL'].startswith('https://')
    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)

    @app.teardown_appcontext
    def close_db(_error):
        db = g.pop('db', None)
        if db is not None:
            db.close()

    with app.app_context():
        db = get_db()
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                debug INTEGER NOT NULL DEFAULT 0, content_version INTEGER NOT NULL DEFAULT 1,
                display_json TEXT NOT NULL, latest_event_id TEXT, last_seen_at TEXT,
                latest_payload TEXT, latest_report_at TEXT, retention_days INTEGER,
                wake_policy_json TEXT, wake_policy_version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS events (
                device_id TEXT NOT NULL REFERENCES devices(device_id), event_id TEXT NOT NULL,
                phase TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(device_id,event_id,phase)
            );
            CREATE INDEX IF NOT EXISTS events_received ON events(received_at);
            CREATE TABLE IF NOT EXISTS event_keys (
                device_id TEXT NOT NULL REFERENCES devices(device_id), event_id TEXT NOT NULL,
                phase TEXT NOT NULL, PRIMARY KEY(device_id,event_id,phase)
            );
            INSERT OR IGNORE INTO event_keys SELECT device_id,event_id,phase FROM events;
            CREATE INDEX IF NOT EXISTS events_device_received ON events(device_id,received_at);
        ''')
        # Multiple Gunicorn workers can start against the same existing database.
        # Acquire the schema-write lock before checking whether the column exists.
        db.execute('BEGIN IMMEDIATE')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(devices)')}
        if 'retention_days' not in columns:
            db.execute('ALTER TABLE devices ADD COLUMN retention_days INTEGER')
        if 'wake_policy_json' not in columns:
            db.execute('ALTER TABLE devices ADD COLUMN wake_policy_json TEXT')
        if 'wake_policy_version' not in columns:
            db.execute('ALTER TABLE devices ADD COLUMN wake_policy_version INTEGER NOT NULL DEFAULT 1')
        db.execute('''INSERT INTO devices(device_id,token_hash,display_json) VALUES(?,?,?)
                      ON CONFLICT(device_id) DO UPDATE SET token_hash=excluded.token_hash''',
                   (app.config['DEVICE_ID'], token_hash(app.config['DEVICE_TOKEN']), json.dumps(DEFAULT_DISPLAY)))
        db.commit()

    def now():
        return app.config['NOW']().astimezone(timezone.utc)

    def csrf_token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return session['csrf']
    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.before_request
    def security_and_retention():
        if request.endpoint == 'static':
            return None
        if request.method == 'POST' and not request.path.startswith('/api/'):
            expected = session.get('csrf', '')
            supplied = request.form.get('csrf_token', '')
            if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
                abort(400, '表单已失效，请刷新页面后重新提交。')
        # NULL means permanent retention. Policies survive restarts and apply to
        # receipt time, independent of a device's clock or late queued reports.
        db = get_db()
        current = now()
        for policy in db.execute('SELECT device_id,retention_days FROM devices WHERE retention_days IS NOT NULL'):
            cutoff = (current - timedelta(days=policy['retention_days'])).isoformat()
            db.execute('DELETE FROM events WHERE device_id=? AND received_at < ?', (policy['device_id'], cutoff))
        db.commit()
        return None

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['Cache-Control'] = 'no-store' if request.endpoint != 'static' else 'public, max-age=3600'
        if app.config['SESSION_COOKIE_SECURE']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error=error.description), error.code
        title, description = HTTP_ERRORS.get(error.code, ('请求失败', '请稍后重试。'))
        if error.description != type(error).description:
            description = error.description
        return render_template('error.html', error=error, error_title=t(title),
                               error_description=t(description)), error.code

    def login_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if session.get('admin') != app.config['ADMIN_USERNAME']:
                return redirect(url_for('login'))
            return fn(*args, **kwargs)
        return wrapped

    def device_request():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(400, 'A JSON object is required.')
        for key in ('device_id', 'event_id'):
            if not isinstance(value.get(key), str) or not IDENTIFIER.fullmatch(value[key]):
                abort(400, f'{key} is required and must be a valid identifier.')
        device = get_db().execute('SELECT * FROM devices WHERE device_id=?', (value['device_id'],)).fetchone()
        authorization = request.headers.get('Authorization', '')
        supplied = authorization[7:] if authorization.startswith('Bearer ') else ''
        if not device or not hmac.compare_digest(token_hash(supplied), device['token_hash']):
            abort(401, 'Invalid device credentials.')
        # Reject nonfinite floats accepted by Python's permissive JSON decoder.
        def bounded_json(item, depth=0):
            if depth > 16:
                return False
            if isinstance(item, float):
                return math.isfinite(item)
            if isinstance(item, str):
                try:
                    item.encode('utf-8')
                    return True
                except UnicodeEncodeError:
                    return False
            if isinstance(item, dict):
                return all(isinstance(key, str) and bounded_json(key, depth + 1)
                           and bounded_json(val, depth + 1) for key, val in item.items())
            if isinstance(item, list):
                return all(bounded_json(val, depth + 1) for val in item)
            return item is None or isinstance(item, (int, bool))
        if not bounded_json(value):
            abort(400, 'JSON contains an invalid or excessively nested value.')
        for key in ('debug_hold', 'alarm_confirmed', 'time_trusted', 'local_debug'):
            if key in value and not isinstance(value[key], bool):
                abort(400, f'{key} must be boolean.')
        for key in ('shutdown', 'metrics', 'server', 'task', 'previous', 'display'):
            if key in value and not isinstance(value[key], dict):
                abort(400, f'{key} must be an object.')
        if 'values' in value.get('metrics', {}) and not isinstance(value['metrics']['values'], dict):
            abort(400, 'metrics.values must be an object.')
        if 'failures' in value and not isinstance(value['failures'], list):
            abort(400, 'failures must be an array.')
        if any(not isinstance(failure, (dict, str)) for failure in value.get('failures', [])):
            abort(400, 'Each failure must be an object or text.')
        return value, device

    @app.post('/api/v1/check-in')
    def check_in():
        payload, device = device_request()
        if timestamp(payload.get('started_at')) is None:
            abort(400, 'started_at must be an ISO timestamp with a UTC offset.')
        wake_policy = None
        if 'wake_schedule' in payload:
            try:
                wake_policy = validate_policy(payload['wake_schedule'])
            except ValueError as exc:
                abort(400, str(exc))
        db = get_db()
        received = now().isoformat()
        inserted = db.execute('INSERT OR IGNORE INTO event_keys VALUES(?,?,?)',
                              (device['device_id'], payload['event_id'], 'check-in')).rowcount
        # Only a first check-in can advance the live event. A retry of an old event
        # and any queued report cannot replace the current boot's state.
        if inserted:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?)',
                       (device['device_id'], payload['event_id'], 'check-in', received, json.dumps(payload)))
            # A fresh contact proves connectivity, but cannot clear the previous
            # cycle's fault before this cycle has a successful final report.
            # Keep only its failures: remote debug never survives between boots.
            previous = json.loads(device['latest_payload'] or '{}')
            current = dict(payload)
            pending = previous.get('failures') or previous.get('pending_failures', [])
            if pending:
                current['pending_failures'] = pending
            db.execute('''UPDATE devices SET latest_event_id=?,latest_payload=?,latest_report_at=NULL,
                          last_seen_at=? WHERE device_id=?''',
                       (payload['event_id'], json.dumps(current), received, device['device_id']))
        else:
            db.execute('UPDATE devices SET last_seen_at=? WHERE device_id=?', (received, device['device_id']))
        if wake_policy is not None:
            # A device can seed an unconfigured server once. Administrator edits
            # and later check-ins always keep the authoritative saved policy.
            db.execute('UPDATE devices SET wake_policy_json=? WHERE device_id=? AND wake_policy_json IS NULL',
                       (json.dumps(wake_policy), device['device_id']))
        db.commit()
        device = db.execute('SELECT * FROM devices WHERE device_id=?', (device['device_id'],)).fetchone()
        reply = dict(debug=bool(device['debug']), content_version=device['content_version'],
                     display=json.loads(device['display_json']))
        if device['wake_policy_json'] is not None:
            reply['wake_plan'] = make_wake_plan(json.loads(device['wake_policy_json']), now(),
                                                version=device['wake_policy_version'])
        return jsonify(reply)

    @app.post('/api/v1/reports')
    def report():
        payload, device = device_request()
        if payload.get('phase') not in ('final', 'outcome'):
            abort(400, 'phase must be final or outcome.')
        if payload.get('next_run') is not None and timestamp(payload['next_run']) is None:
            abort(400, 'next_run must be an ISO timestamp with a UTC offset.')
        received = now().isoformat()
        db = get_db()
        inserted = db.execute('INSERT OR IGNORE INTO event_keys VALUES(?,?,?)',
                              (device['device_id'], payload['event_id'], payload['phase'])).rowcount
        if inserted:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?)',
                       (device['device_id'], payload['event_id'], payload['phase'], received, json.dumps(payload)))
        previous_phase = json.loads(device['latest_payload'] or '{}').get('phase')
        if (inserted and device['latest_event_id'] == payload['event_id']
                and not (previous_phase == 'outcome' and payload['phase'] == 'final')):
            db.execute('''UPDATE devices SET latest_payload=?,latest_report_at=?,last_seen_at=?
                          WHERE device_id=? AND latest_event_id=?
                          AND (?='outcome' OR COALESCE(json_extract(latest_payload,'$.phase'),'')!='outcome')''',
                       (json.dumps(payload), received, received, device['device_id'], payload['event_id'], payload['phase']))
        db.commit()
        return jsonify(accepted=True)

    @app.post('/language')
    def set_language():
        language = request.form.get('language')
        if language not in LANGUAGES:
            abort(400, '不支持此语言。')
        destination = request.form.get('next', '/')
        # The switch returns to the same local page, including history pagination.
        # Never allow a client-supplied external redirect or ambiguous backslashes.
        try:
            parsed = urlsplit(destination)
            safe = (destination.startswith('/') and not destination.startswith('//')
                    and not parsed.scheme and not parsed.netloc and '\\' not in destination
                    and not any(ord(char) < 32 or ord(char) == 127 for char in destination))
        except ValueError:
            safe = False
        response = redirect(destination if safe else url_for('dashboard'))
        response.set_cookie(LANGUAGE_COOKIE, language, max_age=365 * 24 * 3600,
                            httponly=True, secure=app.config['SESSION_COOKIE_SECURE'],
                            samesite='Lax', path='/')
        return response

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            # Always perform the password hash check, including for an unknown username.
            password_ok = check_password_hash(app.config['ADMIN_PASSWORD_HASH'], request.form.get('password', ''))
            if hmac.compare_digest(request.form.get('username', '').encode(), app.config['ADMIN_USERNAME'].encode()) and password_ok:
                session.clear()
                session['admin'] = app.config['ADMIN_USERNAME']
                session.permanent = True
                return redirect(url_for('dashboard'))
            flash('用户名或密码不正确。', 'error')
            return render_template('login.html'), 401
        return render_template('login.html')

    @app.post('/logout')
    @login_required
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    @login_required
    def dashboard():
        devices = []
        current = now()
        for row in get_db().execute('SELECT * FROM devices ORDER BY device_id'):
            item = dict(row)
            item.pop('token_hash')
            item['display'] = json.loads(item.pop('display_json'))
            item['payload'] = json.loads(item.pop('latest_payload') or '{}')
            item['health'], item['health_label'] = health(item, current)
            item['values'] = item['payload'].get('metrics', {}).get('values', {})
            item['battery_percent'] = item['values'].get('battery_percent')
            if item['battery_percent'] is None:
                item['battery_percent'] = item['payload'].get('battery_percent')
            item['wifi_rssi'] = item['values'].get('wifi_rssi')
            item['failures'] = item['payload'].get('failures') or item['payload'].get('pending_failures', [])
            item['expected_next_run'] = expected_next_run(item['payload'])
            item['estimate'] = estimate_battery(get_db(), item['device_id'], current)
            item['wake_policy'] = json.loads(item.pop('wake_policy_json') or 'null')
            wake = item['payload'].get('wake_plan', {})
            item['wake_status'] = wake if isinstance(wake, dict) else {}
            devices.append(item)
        return render_template('dashboard.html', devices=devices, base_url=app.config['BASE_URL'], now=current.isoformat())

    @app.route('/devices/<device_id>/preview.png', methods=['GET', 'POST'])
    @login_required
    def screen_preview(device_id):
        db = get_db()
        device = db.execute('SELECT * FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if device is None:
            abort(404)
        display = json.loads(device['display_json'])
        if request.method == 'POST':
            # Drafts may have empty labels/title while typing. Native bounds and
            # ASCII checks still apply; the save route keeps its stricter rules.
            display = {'title': request.form.get('title', ''), 'status': request.form.get('status', ''),
                       'rows': [{'label': request.form.get(f'label_{i}', ''),
                                 'value': request.form.get(f'value_{i}', '')} for i in range(6)
                                if request.form.get(f'label_{i}') or request.form.get(f'value_{i}')]}
        payload = json.loads(device['latest_payload'] or '{}')
        metrics = payload.get('metrics', {})
        values = metrics.get('values', {})
        battery = values.get('battery_percent')
        if battery is None:
            battery = payload.get('battery_percent')
        battery_text = f'{battery:.1f}%' if type(battery) in (int, float) and 0 <= battery <= 100 else 'N/A'
        clock = 'N/A'
        # The Pi renderer uses the device configuration's TZ, which need not
        # equal the browser zone or the administrator's edited wake schedule.
        check_in = db.execute("SELECT payload FROM events WHERE device_id=? AND event_id=? AND phase='check-in'",
                              (device_id, device['latest_event_id'])).fetchone()
        context = json.loads(check_in['payload']) if check_in else payload
        device_schedule = context.get('wake_schedule')
        zone = device_schedule.get('timezone') if isinstance(device_schedule, dict) else None
        sampled_at = timestamp(metrics.get('timestamp')) or timestamp(payload.get('started_at'))
        if sampled_at and payload.get('time_trusted') is True and isinstance(zone, str):
            try:
                clock = sampled_at.astimezone(ZoneInfo(zone)).strftime('%Y-%m-%d %H:%M')
            except (ZoneInfoNotFoundError, ValueError, OverflowError):
                pass
        try:
            image = get_renderer(app.config['PAGE_RENDERER_LIBRARY']).render(
                display, clock, battery_text, width=app.config['PAGE_PREVIEW_WIDTH'])
        except ValueError:
            abort(400, '预览仅支持显示范围内的英文、数字和可打印 ASCII 符号。')
        except PreviewUnavailable:
            app.logger.exception('Screen preview renderer unavailable')
            abort(503, '屏幕预览暂不可用，请稍后重试。')
        return Response(image, mimetype='image/png')

    @app.route('/devices/<device_id>/settings', methods=['GET', 'POST'])
    @login_required
    def settings(device_id):
        db = get_db()
        device = db.execute('SELECT * FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if device is None:
            abort(404)
        display = json.loads(device['display_json'])
        debug = bool(device['debug'])
        error = None
        if request.method == 'POST':
            debug = request.form.get('debug') == 'on'
            display = {'title': request.form.get('title', ''), 'status': request.form.get('status', ''), 'rows': []}
            for i in range(6):
                label = request.form.get(f'label_{i}', '')
                value = request.form.get(f'value_{i}', '')
                if label or value:
                    display['rows'].append({'label': label, 'value': value})
            try:
                display = validate_display(display)
            except ValueError as exc:
                error = str(exc)
            else:
                changed = display != json.loads(device['display_json'])
                db.execute('UPDATE devices SET debug=?,display_json=?,content_version=content_version+? WHERE device_id=?',
                           (int(debug), json.dumps(display), int(changed), device_id))
                db.commit()
                flash('设置已保存，将在设备下次报到时生效。', 'success')
                return redirect(url_for('settings', device_id=device_id))
        return render_template('settings.html', device=device, display=display, debug=debug,
                               error=error), 400 if error else 200

    @app.route('/devices/<device_id>/wake-plan', methods=['GET', 'POST'])
    @login_required
    def wake_settings(device_id):
        db = get_db()
        device = db.execute('SELECT * FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if device is None:
            abort(404)
        wake_policy = json.loads(device['wake_policy_json'] or 'null')
        wake_timezone = wake_policy['timezone'] if wake_policy else ''
        wake_schedule = '\n'.join(wake_policy['schedule']) if wake_policy else ''
        wake_error = None
        if request.method == 'POST':
            wake_timezone = request.form.get('wake_timezone', '').strip()
            wake_schedule = request.form.get('wake_schedule', '')
            try:
                policy = validate_policy({'timezone': wake_timezone,
                    'schedule': [item for item in re.split(r'[,，;；\s]+', wake_schedule.strip()) if item]})
            except ValueError as exc:
                messages = {
                    'Wake timezone must be an IANA timezone.': '唤醒时区必须是有效的 IANA 时区。',
                    'Wake schedule must contain 1 to 1440 daily times.': '请填写 1 至 1440 个每日唤醒时间。',
                    'Wake times must use HH:MM, from 00:00 to 23:59.': '唤醒时间须使用 HH:MM 格式，范围为 00:00 至 23:59。',
                    'Wake times must not repeat.': '每日唤醒时间不能重复。',
                }
                wake_error = t(messages.get(str(exc), '唤醒计划无效，请检查时区和时间表。'))
            else:
                # Decide the version while holding the write lock so two admin
                # requests cannot accidentally reuse a version for different data.
                db.execute('BEGIN IMMEDIATE')
                saved = db.execute('SELECT wake_policy_json,wake_policy_version FROM devices WHERE device_id=?',
                                   (device_id,)).fetchone()
                changed = policy != json.loads(saved['wake_policy_json'] or 'null')
                version = saved['wake_policy_version'] + int(changed and saved['wake_policy_json'] is not None)
                db.execute('UPDATE devices SET wake_policy_json=?,wake_policy_version=? WHERE device_id=?',
                           (json.dumps(policy), version, device_id))
                db.commit()
                flash('唤醒计划已保存，将在设备下次成功报到时下载并缓存。', 'success')
                return redirect(url_for('wake_settings', device_id=device_id))
        wake_plan = make_wake_plan(wake_policy, now(), device['wake_policy_version']) if wake_policy else None
        return render_template('wake_settings.html', device=device,
                               wake_timezone=wake_timezone, wake_schedule=wake_schedule,
                               wake_plan=wake_plan, wake_error=wake_error), 400 if wake_error else 200

    @app.get('/devices/<device_id>/history')
    @login_required
    def history(device_id):
        db = get_db()
        device = db.execute('SELECT retention_days FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if device is None:
            abort(404)
        page = request.args.get('page', 1, type=int)
        if page < 1 or page > 100000:
            abort(400)
        entries = db.execute('''SELECT event_id,MAX(received_at) AS received_at FROM events
                                WHERE device_id=? GROUP BY event_id
                                ORDER BY received_at DESC,event_id DESC LIMIT 51 OFFSET ?''',
                             (device_id, (page - 1) * 50)).fetchall()
        reports = []
        cycles = []
        for entry in entries[:50]:
            rows = [dict(row) for row in db.execute('''SELECT * FROM events WHERE device_id=? AND event_id=?
                    ORDER BY CASE phase WHEN 'check-in' THEN 0 WHEN 'final' THEN 1 ELSE 2 END''',
                                                   (device_id, entry['event_id']))]
            for row in rows:
                row['payload'] = json.loads(row['payload'])
            reports.extend(rows)
            payload = merge_cycle(rows)
            cycles.append(dict(entry, payload=payload, has_failures=cycle_has_failures(payload),
                               phases=[row['phase'] for row in rows]))
        return render_template('history.html', device_id=device_id, reports=reports, cycles=cycles,
                               page=page, more=len(entries) > 50, retention_days=device['retention_days'],
                               history_data_url=url_for('history_data', device_id=device_id),
                               manage_url=url_for('history_manage', device_id=device_id), retention_options=[3, 7, 15, 30])

    @app.get('/devices/<device_id>/history/data')
    @login_required
    def history_data(device_id):
        db = get_db()
        if db.execute('SELECT 1 FROM devices WHERE device_id=?', (device_id,)).fetchone() is None:
            abort(404)
        days = request.args.get('days', '7')
        if days not in ('3', '7', '15', '30', '90', 'all'):
            abort(400)
        current = now()
        result = build_history_data(db, device_id, current, None if days == 'all' else int(days))
        result['estimate'] = estimate_battery(db, device_id, current)
        return jsonify(result)

    def history_scope(device_id, draft):
        clauses, params = ['device_id=?', 'received_at<=?'], [device_id, draft['asof']]
        if draft['action'] == 'delete_selected':
            ids = draft['event_ids']
            clauses.append('event_id IN (' + ','.join('?' for _ in ids) + ')')
            params.extend(ids)
        elif draft.get('cutoff'):
            clauses.append('received_at < ?')
            params.append(draft['cutoff'])
        else:
            clauses.append('0')  # Permanent retention never removes an existing row.
        return ' AND '.join(clauses), params

    @app.post('/devices/<device_id>/history/manage')
    @login_required
    def history_manage(device_id):
        db = get_db()
        if db.execute('SELECT 1 FROM devices WHERE device_id=?', (device_id,)).fetchone() is None:
            abort(404)
        signer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='history-management')
        action = request.form.get('action')
        if action == 'confirm':
            try:
                draft = signer.loads(request.form.get('confirmation_token', ''), max_age=600)
            except (BadSignature, SignatureExpired):
                abort(400, '确认已失效，请返回历史页面重新预览。')
            nonce = session.get('history_confirmation')
            if (not nonce or draft.get('nonce') != nonce or draft.get('device_id') != device_id):
                abort(400, '确认已失效，请返回历史页面重新预览。')
            scope, params = history_scope(device_id, draft)
            with db:
                if draft['action'] == 'retention':
                    db.execute('UPDATE devices SET retention_days=? WHERE device_id=?',
                               (draft['retention_days'], device_id))
                db.execute('DELETE FROM events WHERE ' + scope, params)
            session.pop('history_confirmation', None)
            flash('保留设置已保存，过期历史已清理。' if draft['action'] == 'retention'
                  else '所选历史已删除，设备当前状态和配置保持不变。', 'success')
            return redirect(url_for('history', device_id=device_id))

        draft = {'action': action, 'device_id': device_id, 'asof': now().isoformat(), 'nonce': secrets.token_hex(24)}
        if action == 'retention':
            raw = request.form.get('retention_days', '')
            if raw == 'custom':
                raw = request.form.get('custom_days', '')
            if raw == 'forever':
                days = None
            elif re.fullmatch(r'[0-9]{1,5}', raw) and 1 <= int(raw) <= 36500:
                days = int(raw)
            else:
                abort(400, '保留天数须为 1–36500 的整数，或选择永久保留。')
            draft['retention_days'] = days
            draft['cutoff'] = (now() - timedelta(days=days)).isoformat() if days else None
            title = t('确认修改保留期限')
            description = t('历史将永久保留，不再自动清理。') if days is None else t(
                '历史将保留最近 {days} 天；此前收到的报告将立即清理，之后自动按此期限清理。', days=days)
        elif action == 'delete_before':
            before = request.form.get('before', '')
            try:
                if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', before):
                    raise ValueError
                local_day = datetime.fromisoformat(before)
            except ValueError:
                abort(400, '请选择有效的删除截止日期。')
            zone_name = request.form.get('timezone', '')
            try:
                if not zone_name or len(zone_name) > 128:
                    raise ValueError
                zone = ZoneInfo(zone_name)
            except (ValueError, ZoneInfoNotFoundError):
                abort(400, '无法识别浏览器时区，请启用 JavaScript 并刷新页面。')
            try:
                cutoff = local_day_start(local_day, zone)
            except (ValueError, OverflowError):
                abort(400, '该日期在所选时区不存在，请选择其他日期。')
            draft['timezone'] = zone.key
            draft['cutoff'] = cutoff.isoformat()
            title = t('确认按日期删除')
            description = t('删除 {date}（{zone}）当天开始之前收到的报告，不包含当天。', date=before, zone=zone.key)
        elif action == 'delete_selected':
            ids = list(dict.fromkeys(request.form.getlist('event_ids')))
            if not ids or len(ids) > 50 or any(not IDENTIFIER.fullmatch(value) for value in ids):
                abort(400, '请选择 1–50 个运行周期。')
            draft['event_ids'] = ids
            title = t('确认删除所选周期')
            description = t('将删除所选运行周期的全部历史报告。')
        else:
            abort(400)
        scope, params = history_scope(device_id, draft)
        counts = db.execute('SELECT COUNT(*),COUNT(DISTINCT event_id) FROM events WHERE ' + scope, params).fetchone()
        session['history_confirmation'] = draft['nonce']
        return render_template('history_confirm.html', device_id=device_id, action=action,
                               title=title, description=description, count=counts[0], cycles=counts[1],
                               confirmation_token=signer.dumps(draft), manage_url=url_for('history_manage', device_id=device_id),
                               language_next=url_for('history', device_id=device_id),
                               cutoff=draft.get('cutoff'), timezone=draft.get('timezone'),
                               details=t('此操作无法撤销。设备当前状态、屏幕内容及调试设置会保留。') if counts[0] else '')

    @app.get('/healthz')
    def healthz():
        get_db().execute('SELECT 1').fetchone()
        return jsonify(status='ok')

    return app


def token_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def expected_next_run(payload):
    if payload.get('alarm_confirmed') is True and payload.get('time_trusted') is True:
        return payload.get('next_run') if timestamp(payload.get('next_run')) else None
    return None


def health(device, now):
    payload = device['payload']
    if not device.get('last_seen_at'):
        return 'unknown', t('等待设备首次报到')
    shutdown = payload.get('shutdown', {})
    if payload.get('debug_hold') is True or shutdown.get('status') == 'debug':
        return 'maintenance', t('维护中 · 保持开机')
    if not device.get('latest_report_at'):
        if payload.get('local_debug') is True:
            return 'maintenance', t('维护中 · 本地调试')
        contact = timestamp(device.get('last_seen_at'))
        if contact and now > contact + timedelta(minutes=10):
            return 'fault', t('周期未完成 · 未收到最终报告')
    expected = timestamp(expected_next_run(payload))
    if expected and now - expected > timedelta(minutes=10):
        return 'missing', t('设备失联 · 超过预计报到时间')
    if payload.get('failures') or payload.get('pending_failures'):
        return 'fault', t('设备上报异常')
    if expected:
        return 'healthy', t('计划休眠')
    return 'unknown', t('下次报到时间未知')
