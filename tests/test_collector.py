"""No hardware: local samples and validated remote page share a single render."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src/scheduler'))
import pisched
spec = importlib.util.spec_from_file_location('collect_metrics', ROOT/'src/tasks/collect_metrics.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.cfg = pisched.validate_config({'schedule':['08:00'], 'log_file':str(folder/'run.log'),
            'metrics_file':str(folder/'metrics.json'), 'state_file':str(folder/'state.json'),
            'outbox_file':str(folder/'outbox.json')})
        self.page = {'title':'MY PAPER', 'status':'READY', 'rows':[{'label':'Test','value':'Server content'}]}

    def run_case(self, wifi='Signal level=-42 dBm', display_rc=0, battery_error=False, page=None):
        samples = ['55','3.7','30'] if not battery_error else [RuntimeError('I/O')]*3
        env = {'PISCHED_PAGE': json.dumps(self.page if page is None else page),
               'PISCHED_HTTP_LATENCY_MS':'12.5', 'PISCHED_PAGE_LABEL_6':'stale'}
        with patch.object(c, 'PiSugarClient') as client, patch.object(c, 'run_command') as run, \
             patch.dict(os.environ, env):
            client.return_value.get.side_effect = samples
            run.side_effect = [pisched.Result(0,wifi), pisched.Result(display_rc,'rendered')]
            code = c.collect(self.cfg,'event-1')
            commands = run.call_args_list
        return code,pisched.read_json(self.cfg['metrics_file']),commands

    def test_samples_and_remote_page_shared_with_renderer(self):
        code,state,commands = self.run_case()
        self.assertEqual(code,0)
        self.assertEqual(state['values']['http_latency_ms'],12.5)
        env = commands[-1].kwargs['env']
        self.assertEqual(env['PISCHED_BATTERY'],'55.0')
        self.assertEqual(env['PISCHED_PAGE_TITLE'],'MY PAPER')
        self.assertEqual(env['PISCHED_PAGE_VALUE_1'],'Server content')
        self.assertNotIn('PISCHED_PAGE_LABEL_6',env)
        self.assertTrue(state['timestamp'].endswith('+00:00'))
        self.assertFalse(list(Path(self.temp.name).glob('*.csv')))
        self.assertFalse(any(call.args[0][0]=='ping' for call in commands))

    def test_unavailable_wifi_still_renders(self):
        code,state,commands = self.run_case(wifi='')
        self.assertEqual(code,1)
        self.assertIsNone(state['values']['wifi_rssi'])
        self.assertEqual(commands[-1].args[0],[self.cfg['display']['path']])

    def test_battery_error_is_missing_value(self):
        _,state,commands = self.run_case(battery_error=True)
        self.assertIsNone(state['values']['battery_percent'])
        self.assertEqual(commands[-1].kwargs['env']['PISCHED_BATTERY'],'')

    def test_display_failure_propagates(self):
        code,state,_ = self.run_case(display_rc=7)
        self.assertEqual(code,1)
        self.assertEqual(state['display_returncode'],7)

    def test_invalid_remote_content_never_launches_display(self):
        code,state,commands = self.run_case(page={'title':'bad\n', 'status':'READY', 'rows':[]})
        self.assertEqual(code,1)
        self.assertEqual(len(commands),1)
        self.assertIn('display',[f['stage'] for f in state['failures']])

    def test_invalid_measurements(self):
        for value in ('nan','inf','I/O','-1','101'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                c.numeric(value,0,100)
