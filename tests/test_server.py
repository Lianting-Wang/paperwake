"""Real SQLite and HTTP test-client coverage; no device hardware side effects."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.server import create_app, get_db, health, validate_display
from src.scheduler.wake_plan import validate_wake_plan
from werkzeug.security import generate_password_hash


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = generate_password_hash('test-admin-password', method='pbkdf2:sha256:1000')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
        self.config = {'TESTING': True, 'DATABASE': str(Path(self.temp.name) / 'server.db'),
                       'SECRET_KEY': 'session-secret-' + 's' * 40, 'DEVICE_TOKEN': 'device-secret-' + 't' * 40,
                       'DEVICE_ID': 'test-screen', 'ADMIN_USERNAME': 'owner',
                       'ADMIN_PASSWORD_HASH': self.password_hash, 'BASE_URL': 'http://192.168.76.109:8088',
                       'NOW': lambda: self.now}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.auth = {'Authorization': 'Bearer ' + self.config['DEVICE_TOKEN']}

    def post(self, path, data, headers=None):
        return self.client.post('/api/v1/' + path, json=data, headers=self.auth if headers is None else headers)

    def check_in(self, event='boot-1', **kwargs):
        return self.post('check-in', dict(device_id='test-screen', event_id=event, started_at=self.now.isoformat(), **kwargs))

    def report(self, event='boot-1', **kwargs):
        return self.post('reports', dict(device_id='test-screen', event_id=event, phase='final', **kwargs))

    def csrf(self, path='/login'):
        response = self.client.get(path)
        return re.search(rb'name="csrf_token" value="([^"]+)"', response.data)[1].decode()

    def login(self):
        return self.client.post('/login', data={'csrf_token': self.csrf(), 'username': 'owner', 'password': 'test-admin-password'})

    def device(self):
        with self.app.app_context():
            item = dict(get_db().execute('SELECT * FROM devices').fetchone())
        item['payload'] = json.loads(item['latest_payload'] or '{}')
        return item

    def settings(self, **updates):
        fields = {'title': 'Today', 'status': 'All systems ready', 'label_0': 'Message', 'value_0': 'Hello, paper!'}
        fields.update(updates)
        fields['csrf_token'] = self.csrf('/devices/test-screen/settings')
        return self.client.post('/devices/test-screen/settings', data=fields)

    def switch_language(self, language, destination='/login'):
        return self.client.post('/language', data={'csrf_token': self.csrf(),
                                                   'language': language, 'next': destination})

    def test_language_defaults_to_chinese_even_with_english_browser(self):
        response = self.client.get('/login', headers={'Accept-Language': 'en-US,en;q=0.9'})
        self.assertIn(b'<html lang="zh-CN">', response.data)
        self.assertIn('用户名'.encode(), response.data)
        self.assertIn(b'English', response.data)
        self.client.set_cookie('epaper_language', 'unsupported')
        self.assertIn(b'<html lang="zh-CN">', self.client.get('/login').data)

    def test_language_selection_survives_login_refresh_and_logout(self):
        response = self.switch_language('en')
        self.assertEqual(response.status_code, 302)
        cookie = next(value for value in response.headers.getlist('Set-Cookie')
                      if value.startswith('epaper_language='))
        self.assertIn('Max-Age=31536000', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)
        self.assertNotIn('Secure;', cookie)
        self.assertIn(b'<html lang="en">', self.client.get('/login').data)
        self.login()
        self.assertIn(b'Your screen, connected.', self.client.get('/').data)
        self.assertIn(b'Your screen, connected.', self.client.get('/').data)
        self.client.post('/logout', data={'csrf_token': self.csrf('/')})
        self.assertIn(b'Username', self.client.get('/login').data)
        self.switch_language('zh')
        self.assertIn('用户名'.encode(), self.client.get('/login').data)

    def test_switch_returns_to_current_page_without_changing_device_settings(self):
        self.login()
        original = self.device()
        path = '/devices/test-screen/history?page=2'
        response = self.switch_language('en', path)
        self.assertEqual(response.headers['Location'], path)
        self.assertIn(b'Page 2', self.client.get(path).data)
        self.assertIn(b'On the screen', self.client.get('/devices/test-screen/settings').data)
        self.assertEqual(self.device()['debug'], original['debug'])
        self.assertEqual(self.device()['display_json'], original['display_json'])
        self.switch_language('zh', '/devices/test-screen/settings')
        self.assertIn('屏幕显示内容'.encode(), self.client.get('/devices/test-screen/settings').data)

    def test_english_statuses_forms_history_and_errors(self):
        self.switch_language('en')
        self.login()
        response = self.settings(title='你好')
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Title accepts English', response.data)
        self.settings()
        self.assertIn(b'Settings apply at the next check-in.', self.client.get('/').data)
        self.check_in()
        self.report(failures=[], debug_hold=True, display={'status': 'ok'}, shutdown_intent='hold')
        dashboard = self.client.get('/').data
        self.assertIn(b'Maintenance', dashboard)
        self.assertIn(b'<dt>Display result</dt><dd>Success</dd>', dashboard)
        self.assertIn(b'Stay awake', dashboard)
        history = self.client.get('/devices/test-screen/history').data
        self.assertIn(b'Final report', history)
        self.assertIn(b'Raw report', history)
        self.assertIn(b'Page not found', self.client.get('/not-a-page').data)
        self.assertIn(b'The form expired', self.client.post('/logout').data)

    def test_language_switch_has_csrf_and_rejects_external_redirects(self):
        self.assertEqual(self.client.get('/language').status_code, 405)
        self.assertEqual(self.client.post('/language', data={'language': 'en'}).status_code, 400)
        self.assertEqual(self.switch_language('fr').status_code, 400)
        for destination in ('https://example.com/', '//example.com/', '/\\example.com/',
                            '/\n//example.com/', '//[broken'):
            with self.subTest(destination=destination):
                response = self.switch_language('en', destination)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers['Location'], '/')

    def test_language_cookie_is_secure_on_https_management_url(self):
        app = create_app(dict(self.config, BASE_URL='https://paper.example.com'))
        client = app.test_client()
        response = client.get('/login', base_url='https://paper.example.com')
        token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)[1].decode()
        response = client.post('/language', data={'csrf_token': token, 'language': 'en', 'next': '/login'},
                               base_url='https://paper.example.com')
        self.assertEqual(response.status_code, 302)
        cookie = next(value for value in response.headers.getlist('Set-Cookie')
                      if value.startswith('epaper_language='))
        self.assertIn('Secure;', cookie)
        self.assertIn(b'Username', client.get('/login', base_url='https://paper.example.com').data)

    def test_checkin_auth_content_and_no_admin_access_with_device_token(self):
        payload = {'device_id': 'test-screen', 'event_id': 'boot-1', 'started_at': self.now.isoformat()}
        for headers in ({}, {'Authorization': 'Bearer incorrect'}):
            self.assertEqual(self.post('check-in', payload, headers).status_code, 401)
        payload['device_id'] = 'other-screen'
        self.assertEqual(self.post('check-in', payload).status_code, 401)
        response = self.check_in(battery_percent=80, local_debug=True)
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json['debug'], False)
        self.assertEqual(response.json['content_version'], 1)
        validate_display(response.json['display'])
        self.assertEqual(self.client.get('/', headers=self.auth).status_code, 302)

    def test_json_validation_and_body_limit(self):
        self.assertEqual(self.client.post('/api/v1/check-in', data='oops', headers=self.auth).status_code, 400)
        self.assertEqual(self.post('check-in', {'device_id': 'test-screen', 'event_id': 'spaces forbidden'}).status_code, 400)
        self.assertEqual(self.post('check-in', {'device_id': 'test-screen', 'event_id': 'boot'}).status_code, 400)
        self.assertEqual(self.check_in(battery_percent=float('nan')).status_code, 400)
        self.assertEqual(self.check_in(huge='x' * 263000).status_code, 413)
        self.assertEqual(self.report(phase_override='unused', failures={}).status_code, 400)
        self.assertEqual(self.report(alarm_confirmed='true').status_code, 400)
        self.assertEqual(self.report(next_run='2026-10-22T13:00:00').status_code, 400)
        self.assertEqual(self.report(metrics={'values': []}).status_code, 400)
        self.assertEqual(self.report(failures=[None]).status_code, 400)
        self.assertEqual(self.check_in(metrics=[]).status_code, 400)
        self.assertEqual(self.check_in(local_debug='false').status_code, 400)

    def test_duplicate_reports_are_idempotent(self):
        self.check_in()
        self.assertTrue(self.report(content_version=1, failures=[]).json['accepted'])
        self.report(content_version=999, failures=['changed retry'])
        with self.app.app_context():
            count = get_db().execute("SELECT COUNT(*) FROM events WHERE phase='final'").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(self.device()['payload']['content_version'], 1)
        self.assertEqual(self.device()['payload']['failures'], [])

    def test_old_reports_and_old_checkin_replays_do_not_replace_current_event(self):
        self.check_in('boot-old')
        self.report('boot-old', failures=['old error'])
        self.now += timedelta(hours=1)
        self.check_in('boot-new')
        self.report('boot-new', failures=[], content_version=2)
        self.report('boot-old', failures=['replacement failure'])
        self.check_in('boot-old')
        item = self.device()
        self.assertEqual(item['latest_event_id'], 'boot-new')
        self.assertEqual(item['payload']['content_version'], 2)
        self.assertEqual(item['payload']['failures'], [])

    def test_shutdown_failure_outcome_replaces_final_and_cannot_be_overwritten(self):
        self.check_in()
        self.report(failures=[], shutdown={'status': 'requesting'})
        outcome = {'device_id': 'test-screen', 'event_id': 'boot-1', 'phase': 'outcome',
                   'failures': [{'stage': 'shutdown', 'message': 'request failed'}],
                   'shutdown': {'status': 'failed'}}
        self.assertEqual(self.post('reports', outcome).status_code, 200)
        self.assertEqual(self.device()['payload']['shutdown']['status'], 'failed')
        self.report(failures=[])
        self.assertEqual(self.device()['payload']['phase'], 'outcome')

    def test_outcome_before_final_has_precedence(self):
        self.check_in()
        self.post('reports', {'device_id': 'test-screen', 'event_id': 'boot-1', 'phase': 'outcome',
                              'failures': ['shutdown failed']})
        self.report(failures=[])
        self.assertEqual(self.device()['payload']['phase'], 'outcome')

    def test_incomplete_cycle_after_ten_minutes_and_local_debug_exception(self):
        self.check_in()
        self.assertEqual(health(self.device(), self.now)[0], 'unknown')
        self.now += timedelta(minutes=11)
        self.assertEqual(health(self.device(), self.now)[0], 'fault')
        self.check_in('local-maintenance', local_debug=True)
        self.now += timedelta(days=1)
        self.assertEqual(health(self.device(), self.now)[0], 'maintenance')

    def test_previous_fault_remains_until_new_cycle_success_and_debug_does_not_carry(self):
        self.check_in('failed-boot')
        self.report('failed-boot', failures=[{'stage': 'display', 'message': 'display failed'}],
                    debug_hold=True, shutdown={'status': 'debug', 'source': 'remote'})
        self.assertEqual(health(self.device(), self.now)[0], 'maintenance')
        self.check_in('pending-boot', local_debug=False)
        pending = self.device()
        self.assertEqual(health(pending, self.now)[0], 'fault')
        self.assertNotIn('debug_hold', pending['payload'])
        self.assertNotIn('shutdown', pending['payload'])
        self.assertEqual(pending['payload']['pending_failures'][0]['message'], 'display failed')
        self.login()
        self.assertIn(b'display failed', self.client.get('/').data)
        with self.app.app_context():
            raw = json.loads(get_db().execute("SELECT payload FROM events WHERE event_id='pending-boot'").fetchone()[0])
        self.assertNotIn('pending_failures', raw)
        self.check_in('another-pending-boot')
        self.assertEqual(health(self.device(), self.now)[0], 'fault')
        self.report('another-pending-boot', failures=[], alarm_confirmed=True, time_trusted=True,
                    next_run=(self.now + timedelta(hours=1)).isoformat(), debug_hold=False)
        self.assertEqual(health(self.device(), self.now)[0], 'healthy')
        self.assertNotIn('pending_failures', self.device()['payload'])
        self.assertNotIn(b'display failed', self.client.get('/').data)

    def test_outage_report_without_checkin_is_historical(self):
        self.check_in('current')
        self.report('current', failures=[], content_version=2)
        self.report('offline-previous', failures=['could not connect'], content_version=1)
        self.assertEqual(self.device()['latest_event_id'], 'current')
        self.assertEqual(self.device()['payload']['content_version'], 2)
        with self.app.app_context():
            self.assertIsNotNone(get_db().execute("SELECT 1 FROM events WHERE event_id='offline-previous'").fetchone())

    def test_clock_correction_does_not_block_new_checkin(self):
        self.check_in('boot-1')
        self.report('boot-1', failures=['error'])
        response = self.post('check-in', {'device_id': 'test-screen', 'event_id': 'boot-2',
                                         'started_at': '2020-01-01T00:00:00+00:00'})
        self.assertEqual(response.status_code, 200)
        self.report('boot-2', failures=[], time_trusted=False)
        self.assertEqual(self.device()['latest_event_id'], 'boot-2')

    def test_admin_login_csrf_logout_and_credentials_separation(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertEqual(self.client.post('/login', data={'username': 'owner', 'password': 'test-admin-password'}).status_code, 400)
        token = self.csrf()
        self.assertEqual(self.client.post('/login', data={'csrf_token': token, 'username': 'wrong', 'password': 'test-admin-password'}).status_code, 401)
        self.assertEqual(self.login().status_code, 302)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('电子纸设备管理'.encode(), response.data)
        self.assertEqual(self.check_in().status_code, 200)
        self.assertEqual(self.post('check-in', {'device_id': 'test-screen', 'event_id': 'boot', 'started_at': self.now.isoformat()}, {}).status_code, 401)
        self.assertEqual(self.client.post('/logout').status_code, 400)
        self.assertEqual(self.client.post('/logout', data={'csrf_token': self.csrf('/')}).status_code, 302)
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_settings_updates_content_debug_version_and_rejects_unicode(self):
        self.login()
        self.assertEqual(self.settings(debug='on').status_code, 302)
        response = self.check_in().json
        self.assertIs(response['debug'], True)
        self.assertEqual(response['content_version'], 2)
        self.assertEqual(response['display']['rows'][0]['value'], 'Hello, paper!')
        self.assertEqual(self.settings().status_code, 302)
        self.assertEqual(self.check_in('boot-2').json['content_version'], 2)
        self.assertIs(self.check_in('boot-2').json['debug'], False)
        self.assertEqual(self.settings(title='你好').status_code, 400)
        self.assertEqual(self.check_in('boot-3').json['display']['title'], 'Today')
        self.assertEqual(self.settings(title='x' * 41).status_code, 400)
        self.assertEqual(self.settings(label_0='', value_0='orphaned value').status_code, 400)
        self.assertEqual(self.client.post('/devices/test-screen/settings', data={'title': 'Bad'}).status_code, 400)

    def test_display_boundaries(self):
        valid = {'title': 't' * 40, 'status': 's' * 24, 'rows': [{'label': 'l' * 20, 'value': 'v' * 48}] * 6}
        self.assertEqual(validate_display(valid), valid)
        for bad in ({'title': '', 'status': 'Ready', 'rows': []},
                    dict(valid, rows=valid['rows'] + [valid['rows'][0]]),
                    dict(valid, status='New\nline'), dict(valid, title='\x7f'),
                    dict(valid, rows=[{'label': 'a', 'value': 'v' * 49}])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_display(bad)

    def test_maintenance_missing_unknown_and_success_clears_fault(self):
        self.check_in()
        self.report(failures=['fault'], alarm_confirmed=True, time_trusted=True,
                    next_run=(self.now + timedelta(hours=1)).isoformat())
        self.assertEqual(health(self.device(), self.now)[0], 'fault')
        self.now += timedelta(hours=1, minutes=11)
        self.assertEqual(health(self.device(), self.now)[0], 'missing')
        self.check_in('debug-boot')
        self.report('debug-boot', debug_hold=True, shutdown={'status': 'debug', 'source': 'local'}, failures=[])
        self.now += timedelta(days=1)
        self.assertEqual(health(self.device(), self.now)[0], 'maintenance')
        self.check_in('healthy-boot')
        self.report('healthy-boot', failures=[], alarm_confirmed=True, time_trusted=True,
                    next_run=(self.now + timedelta(hours=1)).isoformat(), shutdown_intent='poweroff')
        self.assertEqual(health(self.device(), self.now)[0], 'healthy')
        self.check_in('untrusted')
        self.report('untrusted', failures=[], alarm_confirmed=True, time_trusted=False,
                    next_run=(self.now + timedelta(hours=1)).isoformat())
        self.assertEqual(health(self.device(), self.now)[0], 'unknown')

    def test_database_persists_configuration_and_history_across_restart(self):
        self.login()
        self.settings(debug='on')
        self.check_in()
        self.report(failures=['retained'])
        second = create_app(self.config)
        client = second.test_client()
        response = client.post('/api/v1/check-in', json={'device_id': 'test-screen', 'event_id': 'boot-2',
                                                       'started_at': self.now.isoformat()}, headers=self.auth)
        self.assertIs(response.json['debug'], True)
        self.assertEqual(response.json['content_version'], 2)
        with second.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM events WHERE phase='final'").fetchone()[0], 1)

    def save_wake_policy(self, **updates):
        fields = {'wake_timezone': 'America/New_York', 'wake_schedule': '08:00\n12:00\n18:00',
                  'csrf_token': self.csrf('/devices/test-screen/wake-plan')}
        fields.update(updates)
        return self.client.post('/devices/test-screen/wake-plan', data=fields)

    def test_top_navigation_separates_content_wake_schedule_and_history(self):
        self.assertNotIn(b'class="device-nav"', self.client.get('/login').data)
        self.assertEqual(self.client.get('/devices/test-screen/wake-plan').headers['Location'], '/login')
        self.login()
        pages = [('/', '设备概览'), ('/devices/test-screen/settings', '编辑内容'),
                 ('/devices/test-screen/wake-plan', '唤醒计划'), ('/devices/test-screen/history', '报告历史')]
        for language in ('zh', 'en'):
            self.switch_language(language)
            for path, label in pages:
                with self.subTest(language=language, path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    html = response.data.decode()
                    navigation = re.search(r'<nav class="device-nav".*?</nav>', html, re.S)[0]
                    self.assertLess(html.index(navigation), html.index('<main>'))
                    self.assertEqual(navigation.count('aria-current="page"'), 1)
                    for destination, _label in pages:
                        self.assertIn(f'href="{destination}"', navigation)
                    self.assertIn(f'href="{path}" aria-current="page"', navigation)
                    if language == 'zh':
                        self.assertIn(f'aria-current="page">{label}</a>', navigation)
        content = self.client.get('/devices/test-screen/settings').data
        wake = self.client.get('/devices/test-screen/wake-plan').data
        self.assertIn(b'name="title"', content)
        self.assertNotIn(b'name="wake_schedule"', content)
        self.assertIn(b'name="wake_schedule"', wake)
        self.assertNotIn(b'name="title"', wake)
        self.assertNotIn(b'name="debug"', wake)
        self.assertEqual(self.client.get('/devices/missing/wake-plan').status_code, 404)

    def test_saves_and_validation_stay_on_their_respective_pages(self):
        self.login()
        self.assertEqual(self.settings().headers['Location'], '/devices/test-screen/settings')
        self.assertEqual(self.save_wake_policy().headers['Location'], '/devices/test-screen/wake-plan')
        invalid_content = self.settings(title='你好', debug='on')
        self.assertEqual(invalid_content.status_code, 400)
        self.assertIn('value="你好"'.encode(), invalid_content.data)
        self.assertIn(b'name="debug" checked', invalid_content.data)
        self.assertNotIn(b'name="wake_schedule"', invalid_content.data)
        saved = self.device()
        invalid_wake = self.save_wake_policy(wake_schedule='25:00')
        self.assertEqual(invalid_wake.status_code, 400)
        self.assertIn(b'>25:00</textarea>', invalid_wake.data)
        self.assertNotIn(b'name="title"', invalid_wake.data)
        self.assertEqual(self.device()['wake_policy_json'], saved['wake_policy_json'])

    def test_wake_plan_seeds_once_and_is_returned_to_old_devices_after_seeding(self):
        self.assertNotIn('wake_plan', self.check_in().json)
        local = {'timezone': 'America/New_York', 'schedule': ['18:00', '08:00', '12:00']}
        downloaded = self.check_in('upgraded-boot', wake_schedule=local).json['wake_plan']
        self.assertEqual(validate_wake_plan(downloaded), downloaded)
        self.assertEqual(downloaded['schedule'], ['08:00', '12:00', '18:00'])
        self.assertEqual(downloaded['version'], 1)
        # An old replay or an unchanged device configuration cannot undo an edit.
        other = {'timezone': 'UTC', 'schedule': ['00:00']}
        self.assertEqual(self.check_in('old-boot', wake_schedule=other).json['wake_plan'], downloaded)
        self.assertEqual(self.check_in('legacy-boot').json['wake_plan'], downloaded)
        self.now += timedelta(hours=30)
        refreshed = self.check_in('later-boot', wake_schedule=other).json['wake_plan']
        self.assertEqual(refreshed['schedule'], downloaded['schedule'])
        self.assertEqual(refreshed['version'], 1)
        self.assertGreater(refreshed['occurrences'][0], downloaded['occurrences'][0])

    def test_admin_wake_plan_is_independent_authoritative_and_persistent(self):
        self.login()
        self.settings(debug='on')
        original = self.device()
        self.assertEqual(self.save_wake_policy().status_code, 302)
        saved = self.device()
        for field in ('debug', 'display_json', 'content_version'):
            self.assertEqual(saved[field], original[field])
        self.assertEqual(saved['wake_policy_version'], 1)
        self.assertEqual(self.save_wake_policy(wake_schedule='18:00, 12:00 08:00').status_code, 302)
        self.assertEqual(self.device()['wake_policy_version'], 1)
        self.save_wake_policy(wake_schedule='09:00, 17:00', wake_timezone='Europe/London')
        self.assertEqual(self.device()['wake_policy_version'], 2)
        self.settings()  # Content saves must preserve the wake policy.
        downloaded = self.check_in(wake_schedule={'timezone': 'UTC', 'schedule': ['00:00']}).json['wake_plan']
        self.assertEqual(downloaded['timezone'], 'Europe/London')
        self.assertEqual(downloaded['schedule'], ['09:00', '17:00'])
        self.assertEqual(downloaded['version'], 2)
        restarted = create_app(self.config).test_client()
        response = restarted.post('/api/v1/check-in', json={'device_id': 'test-screen', 'event_id': 'restart',
            'started_at': self.now.isoformat()}, headers=self.auth)
        self.assertEqual(response.json['wake_plan'], downloaded)
        self.report('restart', wake_plan={'source': 'cache', 'version': 2, 'timezone': 'Europe/London',
            'valid_until': downloaded['occurrences'][-1], 'future_occurrences': downloaded['occurrences'][:5],
            'occurrence_count': len(downloaded['occurrences']), 'cached': True})
        dashboard = self.client.get('/').data
        self.assertIn('本地缓存'.encode(), dashboard)
        self.assertIn('已持久保存'.encode(), dashboard)
        self.assertIn(b'<time datetime="' + downloaded['occurrences'][-1].encode(), dashboard)
        self.switch_language('en', '/devices/test-screen/wake-plan')
        settings = self.client.get('/devices/test-screen/wake-plan').data
        self.assertIn(b'Daily wake schedule', settings)
        self.assertIn(b'name="wake_timezone" value="Europe/London"', settings)
        self.assertIn(b'Next five wakes', settings)

    def test_invalid_wake_policy_is_nonmutating_and_admin_save_requires_csrf(self):
        for policy in (None, {}, {'timezone': 'UTC', 'schedule': ['24:00']},
                       {'timezone': 'UTC', 'schedule': ['08:00', '08:00']}):
            with self.subTest(policy=policy):
                self.assertEqual(self.check_in(wake_schedule=policy).status_code, 400)
                self.assertIsNone(self.device()['wake_policy_json'])
                self.assertIsNone(self.device()['latest_event_id'])
        self.login()
        self.assertEqual(self.client.post('/devices/test-screen/wake-plan', data={
            'wake_timezone': 'UTC', 'wake_schedule': '08:00'}).status_code, 400)
        for changes in ({'wake_timezone': 'unknown/zone'}, {'wake_schedule': ''},
                        {'wake_schedule': '08:00,08:00'}, {'wake_schedule': '8:00'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.save_wake_policy(**changes).status_code, 400)
                self.assertIsNone(self.device()['wake_policy_json'])
        self.save_wake_policy()
        # Screen validation errors and schedule validation errors stay independent.
        self.assertEqual(self.settings(title='你好').status_code, 400)
        self.assertIsNotNone(self.device()['wake_policy_json'])

    def test_wake_columns_upgrade_is_safe_with_multiple_starting_workers(self):
        self.check_in()
        with self.app.app_context():
            for column in ('wake_policy_json', 'wake_policy_version'):
                get_db().execute(f'ALTER TABLE devices DROP COLUMN {column}')
            get_db().commit()
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(lambda _: create_app(self.config), range(2)))
        self.assertIsNone(self.device()['wake_policy_json'])
        self.assertEqual(self.device()['wake_policy_version'], 1)
        self.assertEqual(self.device()['latest_event_id'], 'boot-1')

    def test_retention_deletes_history_but_keeps_device_configuration(self):
        self.check_in()
        self.report(failures=['retained current snapshot'])
        self.login()
        self.confirm_history(action='retention', retention_days='30')
        self.now += timedelta(days=31)
        self.assertEqual(self.client.get('/healthz').status_code, 200)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
        self.assertEqual(self.device()['payload']['failures'], ['retained current snapshot'])
        self.check_in('current-boot')
        self.check_in('boot-1')
        self.assertEqual(self.device()['latest_event_id'], 'current-boot')

    def history_action(self, **fields):
        fields['csrf_token'] = self.csrf()
        if fields.get('action') == 'delete_before':
            fields.setdefault('timezone', 'UTC')
        return self.client.post('/devices/test-screen/history/manage', data=fields)

    def confirm_history(self, **fields):
        preview = self.history_action(**fields)
        self.assertEqual(preview.status_code, 200)
        token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview.data)[1].decode()
        return self.history_action(action='confirm', confirmation_token=token)

    def test_history_is_permanent_by_default_and_upgrade(self):
        self.check_in()
        self.now += timedelta(days=400)
        self.client.get('/healthz')
        self.assertIsNone(self.device()['retention_days'])
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
            get_db().execute('ALTER TABLE devices DROP COLUMN retention_days')
            get_db().commit()
        with ThreadPoolExecutor(max_workers=2) as workers:
            upgrades = list(workers.map(lambda _: create_app(self.config), range(2)))
        upgraded = upgrades[0]
        upgraded.test_client().get('/healthz')
        with upgraded.app_context():
            self.assertIsNone(get_db().execute('SELECT retention_days FROM devices').fetchone()[0])
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_retention_preview_is_nonmutating_and_confirmed_policy_persists(self):
        self.check_in('old')
        self.now += timedelta(days=8)
        self.check_in('current')
        self.login()
        preview = self.history_action(action='retention', retention_days='7')
        self.assertEqual(preview.status_code, 200)
        self.assertIsNone(self.device()['retention_days'])
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM events').fetchone()[0], 2)
        self.assertEqual(self.confirm_history(action='retention', retention_days='7').status_code, 302)
        self.assertEqual(self.device()['retention_days'], 7)
        restored = create_app(self.config)
        with restored.app_context():
            self.assertEqual(get_db().execute('SELECT retention_days FROM devices').fetchone()[0], 7)
            self.assertEqual(get_db().execute('SELECT event_id FROM events').fetchone()[0], 'current')
        for days in ('3', '15', '30', '42', 'forever'):
            self.assertEqual(self.confirm_history(action='retention', retention_days=days).status_code, 302)
            self.assertEqual(self.device()['retention_days'], None if days == 'forever' else int(days))
        self.confirm_history(action='retention', retention_days='custom', custom_days='11')
        self.assertEqual(self.device()['retention_days'], 11)

    def test_delete_before_is_exclusive_and_preserves_current_state_and_dedup(self):
        self.now = datetime(2026, 10, 20, 23, 59, 59, tzinfo=timezone.utc)
        self.check_in('old')
        self.report('old', failures=['diagnostic'])
        self.now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.check_in('boundary')
        self.report('boundary', failures=[])
        self.login()
        self.assertEqual(self.confirm_history(action='delete_before', before='2026-10-21').status_code, 302)
        with self.app.app_context():
            rows = get_db().execute('SELECT event_id FROM events').fetchall()
            self.assertEqual([row[0] for row in rows], ['boundary', 'boundary'])
        self.assertEqual(self.device()['latest_event_id'], 'boundary')
        self.check_in('old')
        self.report('old', failures=['replay'])
        self.assertEqual(self.device()['latest_event_id'], 'boundary')
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM events WHERE event_id='old'").fetchone()[0], 0)

    def test_local_date_deletion_respects_dst_and_excludes_selected_day(self):
        self.login()
        for before, boundary in [('2026-03-09', datetime(2026, 3, 9, 4, tzinfo=timezone.utc)),
                                 ('2026-11-02', datetime(2026, 11, 2, 5, tzinfo=timezone.utc))]:
            with self.subTest(before=before):
                self.now = boundary - timedelta(seconds=1)
                old = 'old-'+before
                current = 'boundary-'+before
                self.check_in(old)
                self.now = boundary
                self.check_in(current)
                self.confirm_history(action='delete_before', before=before, timezone='America/New_York')
                with self.app.app_context():
                    ids = [row[0] for row in get_db().execute('SELECT event_id FROM events')]
                self.assertNotIn(old, ids)
                self.assertIn(current, ids)

    def test_local_date_deletion_requires_valid_browser_zone_and_real_date(self):
        self.check_in()
        self.login()
        for zone in ('', '../etc/passwd', 'Not/A_Real_Zone'):
            self.assertEqual(self.history_action(action='delete_before', before='2026-10-21', timezone=zone).status_code, 400)
        self.assertEqual(self.history_action(action='delete_before', before='2011-12-30', timezone='Pacific/Apia').status_code, 400)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_local_date_start_handles_repeated_and_skipped_midnight(self):
        from src.server import local_day_start
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('America/Havana')
        spring = local_day_start(datetime(2026, 3, 8), zone)
        autumn = local_day_start(datetime(2026, 11, 1), zone)
        self.assertEqual(spring, datetime(2026, 3, 8, 5, tzinfo=timezone.utc))
        self.assertEqual(spring.astimezone(zone).hour, 1)
        self.assertEqual(autumn, datetime(2026, 11, 1, 4, tzinfo=timezone.utc))
        toronto = local_day_start(datetime(1919, 3, 31), ZoneInfo('America/Toronto'))
        self.assertEqual(toronto, datetime(1919, 3, 31, 4, 30, tzinfo=timezone.utc))

    def test_selected_deletion_is_scoped_to_preview_and_all_existing_phases(self):
        self.check_in('one')
        self.report('one', metrics={'values': {'battery_percent': 70}})
        self.check_in('two')
        self.login()
        self.assertEqual(self.confirm_history(action='delete_selected', event_ids=['one']).status_code, 302)
        with self.app.app_context():
            self.assertEqual([row[0] for row in get_db().execute('SELECT event_id FROM events')], ['two'])
        self.assertEqual(self.device()['latest_event_id'], 'two')

    def test_history_management_requires_login_csrf_and_single_use_confirmation(self):
        self.check_in()
        self.assertEqual(self.history_action(action='retention', retention_days='7').status_code, 302)
        self.login()
        endpoint = '/devices/test-screen/history/manage'
        self.assertEqual(self.client.post(endpoint, data={'action': 'delete_before', 'before': '2100-01-01'}).status_code, 400)
        preview = self.history_action(action='delete_selected', event_ids=['boot-1'])
        token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview.data)[1].decode()
        self.assertEqual(self.history_action(action='confirm', confirmation_token=token+'bad').status_code, 400)
        self.assertEqual(self.history_action(action='confirm', confirmation_token=token).status_code, 302)
        self.assertEqual(self.history_action(action='confirm', confirmation_token=token).status_code, 400)
        for fields in ({'action': 'delete_before', 'before': '2026-02-30'},
                       {'action': 'delete_before', 'before': '20261021'},
                       {'action': 'retention', 'retention_days': '0'},
                       {'action': 'retention', 'retention_days': '-7'},
                       {'action': 'retention', 'retention_days': '36501'},
                       {'action': 'delete_selected', 'event_ids': []},
                       {'action': 'delete_selected', 'event_ids': ['bad id']}):
            self.assertEqual(self.history_action(**fields).status_code, 400)

    def test_delete_preview_excludes_later_reports_and_confirmation_expires(self):
        self.check_in('old')
        self.login()
        preview = self.history_action(action='delete_before', before='2100-01-01')
        token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview.data)[1].decode()
        self.now += timedelta(seconds=1)
        self.check_in('new')
        self.assertEqual(self.history_action(action='confirm', confirmation_token=token).status_code, 302)
        with self.app.app_context():
            self.assertEqual([row[0] for row in get_db().execute('SELECT event_id FROM events')], ['new'])
        preview = self.history_action(action='delete_selected', event_ids=['new'])
        token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview.data)[1].decode()
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=int(time.time()) + 601):
            self.assertEqual(self.history_action(action='confirm', confirmation_token=token).status_code, 400)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_history_charts_require_auth_and_use_one_sample_per_cycle(self):
        path = '/devices/test-screen/history/data'
        self.assertEqual(self.client.get(path).status_code, 302)
        self.check_in(time_trusted=True, battery_percent=80)
        self.report(metrics={'values': {'battery_percent': 79, 'battery_voltage': 4.1}}, elapsed_seconds=20)
        self.login()
        response = self.client.get(path+'?days=7')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['summary']['cycles'], 1)
        self.assertEqual(response.json['summary']['reports'], 2)
        self.assertEqual(response.json['series'][0]['battery_percent'], 79)
        self.assertEqual(self.client.get(path+'?days=invalid').status_code, 400)
        self.assertEqual(self.client.get('/devices/unknown/history/data').status_code, 404)

    def test_battery_estimate_is_available_on_history_api_and_dashboard(self):
        for index, percent in enumerate((90, 87, 84)):
            self.now = datetime(2026, 10, 22, index * 6, tzinfo=timezone.utc)
            event = 'battery-'+str(index)
            self.check_in(event, time_trusted=True)
            self.report(event, time_trusted=True, started_at=self.now.isoformat(),
                        metrics={'timestamp': self.now.isoformat(),
                                 'values': {'battery_percent': percent, 'battery_voltage': 4.1 - index * .05}})
        self.login()
        estimate = self.client.get('/devices/test-screen/history/data?days=all').json['estimate']
        self.assertEqual(estimate['status'], 'ready')
        self.assertEqual(estimate['daily_consumption_pp'], 12)
        self.assertEqual(estimate['reserve_percent'], 10)
        self.assertAlmostEqual(estimate['remaining_days'], 74 / 12, places=3)
        self.assertEqual(estimate['voltage_sample_count'], 3)
        self.assertEqual(estimate['first_voltage'], 4.1)
        self.assertEqual(estimate['latest_voltage'], 4.0)
        dashboard = self.client.get('/')
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn('估计日耗电'.encode(), dashboard.data)
        self.assertIn('预计剩余续航'.encode(), dashboard.data)
        self.assertIn('电压依据：4.100 → 4.000 V，3 个有效样本。'.encode(), dashboard.data)
        self.assertIn(b'estimateVoltageBasis', self.client.get('/devices/test-screen/history').data)

    def test_cycle_details_and_charts_share_failure_definition(self):
        self.check_in()
        self.report(metrics={'failures': ['sensor unavailable']}, task={'status': 'failed'})
        self.login()
        response = self.client.get('/devices/test-screen/history')
        self.assertIn('有异常'.encode(), response.data)
        self.assertIn(b'sensor unavailable', response.data)
        data = self.client.get('/devices/test-screen/history/data').json
        self.assertEqual(data['summary']['failure_cycles'], 1)

    def test_language_switch_on_confirmation_returns_to_history(self):
        self.check_in()
        self.login()
        preview = self.history_action(action='delete_selected', event_ids=['boot-1'])
        target = re.search(rb'name="next" value="([^"]+)"', preview.data)[1].decode()
        self.assertEqual(target, '/devices/test-screen/history')
        response = self.switch_language('en', target)
        self.assertEqual(self.client.get(response.headers['Location']).status_code, 200)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_dashboard_and_history_render_metrics_and_escaped_content(self):
        self.check_in()
        self.report(failures=[{'stage': 'display', 'message': '<script>bad</script>'}],
                    metrics={'values': {'battery_percent': 71, 'wifi_rssi': -48}},
                    shutdown={'status': 'requesting'}, shutdown_intent='poweroff', content_version=1)
        self.login()
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'71%', response.data)
        self.assertIn(b'-48 dBm', response.data)
        self.assertNotIn(b'<script>bad</script>', response.data)
        self.assertIn('尚未确认实际断电'.encode(), response.data)
        history = self.client.get('/devices/test-screen/history')
        self.assertEqual(history.status_code, 200)
        self.assertIn(b'boot-1', history.data)
        self.assertEqual(self.client.get('/devices/unknown/history').status_code, 404)
        self.assertEqual(self.client.get('/devices/test-screen/history?page=-1').status_code, 400)

    def test_session_cookie_security_tracks_management_url(self):
        response = self.client.get('/login')
        self.assertNotIn('Secure;', response.headers['Set-Cookie'])
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        secure_app = create_app(dict(self.config, BASE_URL='https://paper.example.com'))
        secure_response = secure_app.test_client().get('/login', base_url='https://paper.example.com')
        self.assertIn('Secure;', secure_response.headers['Set-Cookie'])
        self.assertEqual(secure_response.headers['X-Frame-Options'], 'DENY')
        self.assertIn('max-age', secure_response.headers['Strict-Transport-Security'])

    def test_non_ascii_credentials_are_rejected_without_server_error(self):
        token = self.csrf()
        self.assertEqual(self.client.post('/login', data={'csrf_token': token, 'username': '用户',
                                                        'password': 'incorrect'}).status_code, 401)
        self.assertEqual(self.client.post('/login', data={'csrf_token': '字符', 'username': 'owner',
                                                        'password': 'incorrect'}).status_code, 400)

    def test_actual_scheduler_report_shape_is_accepted_and_visible(self):
        self.check_in(time_trusted=True, local_debug=False, previous={})
        response = self.report(started_at=self.now.isoformat(), finished_at=self.now.isoformat(),
            time_trusted=True, alarm_confirmed=True, next_run=(self.now + timedelta(hours=1)).isoformat(),
            metrics={'display_returncode': 0, 'values': {'battery_percent': 83.4, 'battery_voltage': 4.1, 'temperature': 41.0,
                                'wifi_rssi': -49}}, server={'status': 'connected', 'latency_ms': 7.3},
            display={'status': 'ok'}, failures=[], debug_hold=False, shutdown_intent='poweroff',
            shutdown={'status': 'requesting'}, content_version=1)
        self.assertEqual(response.status_code, 200)
        self.login()
        dashboard = self.client.get('/')
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'7.3 ms', dashboard.data)
        self.assertIn(b'83.4%', dashboard.data)
        self.assertIn('<dt>屏幕刷新</dt><dd>成功</dd>'.encode(), dashboard.data)

    def test_display_result_falls_back_to_metrics_returncode(self):
        self.check_in()
        self.report(metrics={'display_returncode': 0, 'values': {}}, failures=[])
        self.login()
        dashboard = self.client.get('/')
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn('<dt>屏幕刷新</dt><dd>成功</dd>'.encode(), dashboard.data)

    def test_missing_metrics_use_dash_without_units_and_battery_falls_back(self):
        self.check_in('missing-metrics')
        self.report('missing-metrics', metrics={'values': {'battery_percent': None, 'wifi_rssi': None}})
        self.login()
        dashboard = self.client.get('/')
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn('<dt>电量</dt><dd>—</dd>'.encode(), dashboard.data)
        self.assertIn('<dt>Wi-Fi 信号</dt><dd>—</dd>'.encode(), dashboard.data)
        self.assertNotIn(b'None', dashboard.data)
        self.check_in('fallback-battery')
        self.report('fallback-battery', battery_percent=0,
                    metrics={'values': {'battery_percent': None, 'wifi_rssi': 0}})
        dashboard = self.client.get('/')
        self.assertIn('<dt>电量</dt><dd>0%</dd>'.encode(), dashboard.data)
        self.assertIn('<dt>Wi-Fi 信号</dt><dd>0 dBm</dd>'.encode(), dashboard.data)

    def test_extreme_future_wakeup_cannot_overflow_dashboard(self):
        self.check_in()
        self.assertEqual(self.report(alarm_confirmed=True, time_trusted=True,
                                     next_run='9999-12-31T23:59:59+00:00').status_code, 200)
        self.login()
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_surrogate_strings_and_dictionary_keys_are_rejected(self):
        self.check_in()
        self.assertEqual(self.report(failures=['bad \ud800']).status_code, 400)
        self.assertEqual(self.report(metrics={'values': {'\ud800': 42}}).status_code, 400)
        self.assertEqual(self.report(failures=['Valid Unicode: 温度']).status_code, 200)
        self.login()
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_missing_secrets_fail_closed(self):
        for changes in ({'SECRET_KEY': ''}, {'DEVICE_TOKEN': 'short'}, {'ADMIN_PASSWORD_HASH': 'plaintext'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                create_app(dict(self.config, **changes))


if __name__ == '__main__':
    unittest.main()
