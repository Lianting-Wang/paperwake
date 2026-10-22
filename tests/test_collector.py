"""Isolated measurements: substitute every external command and PiSugar read."""
import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src/scheduler'))
import pisched
spec=importlib.util.spec_from_file_location('collect_metrics', ROOT/'src/tasks/collect_metrics.py')
c=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name)
        self.cfg=pisched.validate_config({'schedule':['08:00'], 'log_file':str(folder/'run.log'),
                                         'metrics_file':str(folder/'metrics.json'), 'state_file':str(folder/'state.json')})
        self.ping='20 packets transmitted, 20 received, 0% packet loss\nrtt min/avg/max/mdev = 1.000/5.941/8.000/1.000 ms\n'

    def run_case(self, wifi=None, ping=None, display_rc=0, battery_error=False):
        samples=['55','3.7','30'] if not battery_error else [RuntimeError('I/O')]*3
        with patch.object(c, 'PiSugarClient') as client, patch.object(c, 'run_command') as run:
            client.return_value.get.side_effect=samples
            run.side_effect=[pisched.Result(0, 'Signal level=-42 dBm' if wifi is None else wifi),
                             pisched.Result(0,self.ping if ping is None else ping), pisched.Result(display_rc,'rendered')]
            code=c.collect(self.cfg, 'event-1')
            commands=run.call_args_list
        return code,pisched.read_json(self.cfg['metrics_file']),commands

    def test_network_loss_still_writes_csv_and_renders(self):
        code,state,commands=self.run_case(wifi='', ping='20 transmitted, 0 received, 100% packet loss\n')
        self.assertEqual(code,1)
        self.assertEqual(state['values']['ping_loss'],100)
        self.assertIsNone(state['values']['ping_avg_latency'])
        self.assertEqual(commands[-1].args[0],[self.cfg['display']['path']])
        with (Path(self.temp.name)/'network_metrics.csv').open() as handle:
            rows=list(csv.reader(handle))
        self.assertEqual(rows[1][-1],'NaN')

    def test_samples_shared_with_renderer_and_metrics(self):
        code,state,commands=self.run_case()
        self.assertEqual(code,0)
        self.assertEqual(state['values']['ping_avg_latency'],5.941)
        self.assertEqual(commands[-1].kwargs['env']['PISCHED_BATTERY'],'55.0')
        self.assertTrue(state['timestamp'].endswith('+00:00'))

    def test_battery_protocol_error_never_written_as_text(self):
        code,state,commands=self.run_case(battery_error=True)
        self.assertEqual(code,1)
        self.assertIsNone(state['values']['battery_percent'])
        self.assertEqual(commands[-1].kwargs['env']['PISCHED_BATTERY'],'')

    def test_display_failure_propagates(self):
        code,state,_=self.run_case(display_rc=7)
        self.assertEqual(code,1)
        self.assertEqual(state['display_returncode'],7)
        self.assertIn('display',[item['stage'] for item in state['failures']])

    def test_missing_programs_still_attempt_display(self):
        with patch.object(c, 'PiSugarClient') as client, patch.object(c, 'run_command', side_effect=FileNotFoundError) as run:
            client.return_value.get.side_effect=['55','3.7','30']
            self.assertEqual(c.collect(self.cfg,'event'),1)
        self.assertEqual(run.call_count,3)
        self.assertIn('display',[item['stage'] for item in pisched.read_json(self.cfg['metrics_file'])['failures']])

    def test_invalid_numbers_rejected(self):
        for value in ('nan','inf','I/O','-1','101'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                c.numeric(value,0,100)
