"""Native framebuffer/PNG and authenticated draft-preview regressions; no hardware."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from werkzeug.security import generate_password_hash
from src.server import create_app, get_db
from src.server.preview import PageRenderer, PreviewUnavailable, SOURCE_ROOT, build_library


def read_png(data):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    offset, packed = 8, b''
    while offset < len(data):
        length = struct.unpack('!I', data[offset:offset + 4])[0]
        kind, body = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        assert zlib.crc32(kind + body) & 0xffffffff == struct.unpack('!I', data[offset + 8 + length:offset + 12 + length])[0]
        if kind == b'IHDR':
            width, height, bits, color, *_ = struct.unpack('!IIBBBBB', body)
            assert bits == 4 and color == 0
        elif kind == b'IDAT':
            packed += body
        offset += 12 + length
    pixels = zlib.decompress(packed)
    assert len(pixels) == (width // 2 + 1) * height
    def pixel(x, y):
        assert pixels[y * (width // 2 + 1)] == 0
        value = pixels[y * (width // 2 + 1) + 1 + x // 2]
        return value >> 4 if x % 2 == 0 else value & 15
    return width, height, pixel


class ScreenPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.native.cleanup)
        cls.library = build_library(Path(cls.native.name) / 'preview.so')
        cls.renderer = PageRenderer(cls.library)
        cls.password_hash = generate_password_hash('preview-password', method='pbkdf2:sha256:1000')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({'TESTING': True, 'DATABASE': str(Path(self.temp.name) / 'test.db'),
            'SECRET_KEY': 's' * 40, 'DEVICE_ID': 'screen', 'DEVICE_TOKEN': 't' * 40,
            'ADMIN_USERNAME': 'preview', 'ADMIN_PASSWORD_HASH': self.password_hash,
            'PAGE_RENDERER_LIBRARY': str(self.library), 'NOW': lambda: datetime(2026, 10, 27, 21, 1, tzinfo=timezone.utc)})
        self.client = self.app.test_client()
        self.path = '/devices/screen/preview.png'
        self.page = {'title': 'EPAPER ONLINE', 'status': 'SERVER CONNECTED', 'rows': [
            {'label': 'Source', 'value': '192.168.76.109:8088'},
            {'label': 'Mode', 'value': 'Remote debug'}]}
        with self.app.app_context():
            get_db().execute('UPDATE devices SET display_json=?', (json.dumps(self.page),))
            get_db().commit()

    def login(self):
        token = re.search(rb'name="csrf_token" value="([^"]+)"', self.client.get('/login').data)[1].decode()
        self.client.post('/login', data={'csrf_token': token, 'username': 'preview', 'password': 'preview-password'})
        with self.client.session_transaction() as state:
            self.token = state['csrf'] if 'csrf' in state else None
        if self.token is None:
            self.client.get('/')
            with self.client.session_transaction() as state:
                self.token = state['csrf']

    def state(self):
        with self.app.app_context():
            return tuple(tuple(row) for row in get_db().execute('SELECT * FROM devices')), tuple(
                tuple(row) for row in get_db().execute('SELECT * FROM events'))

    def test_png_geometry_unicode_grays_and_wrapping(self):
        from src.scheduler.display_model import default_layout
        page = dict(self.page, title='中文 / English', rows=[{'label':'名称','value':'中文测试' * 12}] * 6)
        for width in (1872,1856):
            image, issues = self.renderer.preview(page, '2026-10-27 17:01', '100.0%', width)
            self.assertEqual(issues, [])
            w,h,pixel=read_png(image)
            self.assertEqual((w,h),(width,1404))
            self.assertEqual(pixel(10,10),15)
            self.assertEqual(pixel(100,156),0)
            self.assertTrue(any(pixel(x,y)<15 for x in range(96,800) for y in range(218,350)))
        layout=default_layout()
        layout['elements'][3]['height']=16
        _,issues=self.renderer.preview(dict(page,layout=layout))
        self.assertIn({'element_id':'title','code':'overflow'},issues)
        with self.assertRaises(ValueError):
            self.renderer.render(dict(page,layout=layout))
        _,issues=self.renderer.preview(dict(self.page,title='😀'))
        self.assertIn({'element_id':'title','code':'missing_glyph'},issues)

    def test_parallel_drafts_do_not_share_framebuffers(self):
        pages=[dict(self.page,title=f'DRAFT {index}') for index in range(8)]
        expected=[self.renderer.render(page) for page in pages]
        self.renderer._render.cache_clear()
        with ThreadPoolExecutor(max_workers=6) as workers:
            actual=list(workers.map(self.renderer.render,pages))
        self.assertEqual(expected,actual)
        self.assertEqual(len(set(actual)),len(actual))

    def test_preview_requires_admin_and_csrf_and_never_saves_drafts(self):
        self.assertEqual(self.client.get(self.path).headers['Location'], '/login')
        self.login()
        before = self.state()
        self.assertEqual(self.client.post(self.path, data={'title': 'draft'}).status_code, 400)
        self.assertEqual(self.client.get('/devices/missing/preview.png').status_code, 404)
        result = self.client.get(self.path)
        self.assertEqual(result.mimetype, 'image/png')
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        draft = self.client.post(self.path, data={'csrf_token': self.token, 'title': 'UNSAVED',
            'status': '', 'value_0': 'Partial row without a label', 'debug': 'on'})
        self.assertEqual(draft.status_code, 200)
        self.assertNotEqual(draft.data, result.data)
        self.assertEqual(self.state(), before)
        for title in ('x' * 41, 'bad\nline'):
            self.assertEqual(self.client.post(self.path, data={'csrf_token': self.token, 'title': title}).status_code, 400)
        self.assertEqual(self.state(), before)

    def test_report_context_uses_device_zone_even_when_wake_policy_changes(self):
        auth = {'Authorization': 'Bearer ' + 't' * 40}
        started = '2026-10-27T21:00:00+00:00'
        self.client.post('/api/v1/check-in', headers=auth, json={'device_id': 'screen', 'event_id': 'boot',
            'started_at': started, 'wake_schedule': {'timezone': 'America/New_York', 'schedule': ['08:00']}})
        self.client.post('/api/v1/reports', headers=auth, json={'device_id': 'screen', 'event_id': 'boot',
            'phase': 'final', 'time_trusted': True, 'started_at': started,
            'metrics': {'timestamp': '2026-10-27T21:01:00+00:00', 'values': {'battery_percent': 100}}})
        with self.app.app_context():
            get_db().execute('UPDATE devices SET wake_policy_json=?', (json.dumps({'timezone': 'Asia/Shanghai', 'schedule': ['12:00']}),))
            get_db().commit()
        self.login()
        before = self.state()
        result = self.client.get(self.path)
        self.assertEqual(result.data, self.renderer.render(self.page, '2026-10-27 17:01 UTC-0400', '100.0%'))
        self.assertEqual(self.state(), before)
        with self.app.app_context():
            get_db().execute('DELETE FROM events')
            get_db().commit()
        self.assertEqual(self.client.get(self.path).data, self.renderer.render(self.page, 'N/A', '100.0%'))

    def test_unknown_report_context_stays_na_and_missing_or_stale_renderer_fails(self):
        self.login()
        self.assertEqual(self.client.get(self.path).data, self.renderer.render(self.page))
        self.app.config['PAGE_RENDERER_LIBRARY'] = str(Path(self.temp.name) / 'missing.so')
        with self.assertLogs(self.app.logger, level='ERROR'):
            self.assertEqual(self.client.get(self.path).status_code, 503)
        with patch('src.server.preview.source_revision', return_value='changed-style'):
            with self.assertRaises(PreviewUnavailable):
                PageRenderer(self.library)
        for page in (dict(self.page, rows=['bad']), dict(self.page, rows=[{'label': 'x', 'value': ''}] * 7)):
            with self.assertRaises(ValueError):
                self.renderer.render(page)


if __name__ == '__main__':
    unittest.main()
