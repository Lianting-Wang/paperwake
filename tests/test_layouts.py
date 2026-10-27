"""Published layouts are immutable snapshots; draft requests never touch hardware."""
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from werkzeug.security import generate_password_hash
from src.server import create_app, get_db
from src.scheduler.display_model import default_layout, validate_display, validate_layout


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config=dict(TESTING=True,DATABASE=str(Path(self.folder.name)/'server.db'),
                         SECRET_KEY='s'*40,DEVICE_TOKEN='t'*40,DEVICE_ID='paper',
                         ADMIN_USERNAME='admin',ADMIN_PASSWORD_HASH=generate_password_hash('pass',method='pbkdf2:sha256:1000'))
        self.app=create_app(self.config)
        self.client=self.app.test_client()
        token=re.search(rb'name="csrf_token" value="([^"]+)"',self.client.get('/login').data)[1].decode()
        self.client.post('/login',data=dict(csrf_token=token,username='admin',password='pass'))
        self.client.get('/')
        with self.client.session_transaction() as state:
            self.headers={'X-CSRF-Token':state['csrf']}
        self.url='/devices/paper/layouts'

    def post(self,path='',**body):
        return self.client.post(self.url+path,json=body,headers=self.headers)

    def device(self):
        with self.app.app_context():
            return dict(get_db().execute('SELECT * FROM devices').fetchone())

    def clone(self):
        response=self.post(source_id='editorial',name='自定义版式')
        self.assertEqual(response.status_code,201)
        return response.json

    def test_draft_save_reload_and_publish_snapshot(self):
        original=self.device()
        custom=self.clone()
        layout=custom['layout']; layout['elements'][3]['size']=80
        saved=self.post('/'+custom['id'],revision=1,name='纸刊 · 大字',layout=layout)
        self.assertEqual(saved.status_code,200)
        self.assertEqual(saved.json['revision'],2)
        self.assertEqual(self.device(),original)
        published=self.post('/'+custom['id']+'/apply',revision=2)
        self.assertEqual(published.status_code,200)
        self.assertEqual(self.device()['content_version'],original['content_version']+1)
        active=self.device()['active_layout_json']
        layout=saved.json['layout']; layout['elements'][3]['size']=64
        self.assertEqual(self.post('/'+custom['id'],revision=2,name='草稿',layout=layout).status_code,200)
        self.assertEqual(self.device()['active_layout_json'],active)
        restarted=create_app(self.config)
        with restarted.app_context():
            self.assertEqual(get_db().execute('SELECT revision FROM layouts WHERE id=?',(custom['id'],)).fetchone()[0],3)
        response=self.client.post('/api/v1/check-in',headers={'Authorization':'Bearer '+'t'*40},
                                  json={'device_id':'paper','event_id':'new','started_at':'2026-10-27T12:00:00+00:00'})
        self.assertEqual(response.json['display']['layout'],json.loads(active))
        self.assertEqual(response.json['display']['rows'],json.loads(original['display_json'])['rows'])

    def test_overflow_and_missing_glyph_save_but_do_not_publish(self):
        custom=self.clone(); layout=custom['layout']
        layout['elements'][3]['height']=16
        response=self.post('/'+custom['id'],revision=1,name=custom['name'],layout=layout)
        self.assertEqual(response.status_code,200)
        before=self.device()
        response=self.post('/'+custom['id']+'/apply',revision=2)
        self.assertEqual(response.status_code,400)
        self.assertIn({'element_id':'title','code':'overflow'},response.json['issues'])
        self.assertEqual(self.device(),before)

    def test_preview_auth_csrf_and_native_diagnostics_are_nonmutating(self):
        before=self.device(); layout=default_layout()
        self.assertEqual(self.client.post('/devices/paper/layout-preview',json={'layout':layout}).status_code,400)
        response=self.client.post('/devices/paper/layout-preview',json={'layout':layout},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json['issues'])
        self.assertTrue(response.json['png_base64'].startswith('iVBOR'))
        self.assertEqual(self.device(),before)
        other=self.app.test_client()
        self.assertEqual(other.get(self.url).status_code,302)
        self.assertEqual(other.post(self.url,json={}).status_code,400)

    def test_stale_edit_cannot_overwrite_or_apply(self):
        custom=self.clone()
        self.assertEqual(self.post('/'+custom['id'],revision=1,name='新的名字',layout=custom['layout']).status_code,200)
        self.assertEqual(self.post('/'+custom['id'],revision=1,name='旧窗口',layout=custom['layout']).status_code,409)
        self.assertEqual(self.post('/'+custom['id']+'/apply',revision=1).status_code,409)

    def test_builtin_protection_delete_and_idempotent_apply(self):
        before=self.device()
        self.assertEqual(self.post('/editorial',revision=1,name='changed',layout=default_layout()).status_code,400)
        self.assertEqual(self.post('/editorial/apply',revision=1).status_code,200)
        self.assertEqual(self.device(),before)
        custom=self.clone()
        self.assertEqual(self.post('/'+custom['id'],revision=1,action='delete').status_code,200)
        self.assertEqual(self.post('/'+custom['id']+'/apply',revision=1).status_code,404)

    def test_canvas_contract_unicode_and_limits(self):
        content=dict(title='中文标题',status='就绪',rows=[dict(label='信息',value='中文\nEnglish')])
        self.assertEqual(validate_display(content),content)
        for key,value in [('x',-1),('width',1873),('font','../../font'),('binding','arbitrary'),('gray',16),('line_height',float('nan'))]:
            layout=default_layout(); layout['elements'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): validate_layout(layout)
        for invalid in ['bad\x00text','bad\ud800','bad\u200btext']:
            with self.assertRaises(ValueError): validate_display(dict(content,title=invalid))
        layout=default_layout(); layout['elements'][1]['id']=layout['elements'][0]['id']
        with self.assertRaises(ValueError): validate_layout(layout)

    def test_migration_preserves_data_and_initializes_paper_once(self):
        with self.app.app_context():
            db=get_db(); db.execute('UPDATE devices SET active_layout_json=NULL,debug=1,content_version=9')
            db.commit()
        create_app(self.config)
        first=self.device()
        self.assertEqual(first['content_version'],10)
        self.assertEqual(first['debug'],1)
        self.assertEqual(json.loads(first['active_layout_json'])['id'],'editorial')
        create_app(self.config)
        self.assertEqual(self.device(),first)
