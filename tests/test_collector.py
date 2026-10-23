"""No hardware: local samples and validated remote page share a single render."""
import importlib.util
from datetime import datetime, timedelta, timezone
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
        def get(key, validator=None):
            value = samples.pop(0)
            if isinstance(value, Exception):
                raise value
            return validator(value) if validator is not None else value
        def command(argv, *args, **kwargs):
            return pisched.Result(0,wifi) if argv[0] == 'iwconfig' else pisched.Result(display_rc,'rendered')
        with patch.object(c, 'PiSugarClient') as client, patch.object(c, 'run_command') as run, \
             patch.object(c.time, 'sleep'), patch.dict(os.environ, env):
            client.return_value.get.side_effect = get
            run.side_effect = command
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
        self.assertEqual(sum(call.args[0][0] == 'iwconfig' for call in commands),3)

    def test_battery_error_is_missing_value(self):
        _,state,commands = self.run_case(battery_error=True)
        self.assertIsNone(state['values']['battery_percent'])
        self.assertEqual(commands[-1].kwargs['env']['PISCHED_BATTERY'],'')

    def test_display_failure_propagates(self):
        code,state,commands = self.run_case(display_rc=7)
        self.assertEqual(code,1)
        self.assertEqual(state['display_returncode'],7)
        self.assertEqual(sum(call.args[0] == [self.cfg['display']['path']] for call in commands),1)

    def test_invalid_remote_content_never_launches_display(self):
        code,state,commands = self.run_case(page={'title':'bad\n', 'status':'READY', 'rows':[]})
        self.assertEqual(code,1)
        self.assertEqual(len(commands),1)
        self.assertIn('display',[f['stage'] for f in state['failures']])

    def test_invalid_measurements(self):
        for value in ('nan','inf','I/O','-1','101'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                c.numeric(value,0,100)

    def timed_case(self, responses, wifi_results=None, task_seconds=None, command_seconds=0):
        """Exercise real retry/budget code with a clock and socket-free responses."""
        clock = [0.0]
        base = datetime(2026,10,23,12,tzinfo=timezone.utc)
        class ClockDateTime:
            @staticmethod
            def now(tz=None):
                return (base + timedelta(seconds=clock[0])).astimezone(tz)
        def advance(seconds):
            clock[0] += seconds
        def socket_command(command):
            advance(command_seconds)
            return next(responses)
        wifi = iter(wifi_results or [pisched.Result(0,'Signal level=-42 dBm')])
        def command(argv, seconds, **kwargs):
            if argv[0] == 'iwconfig':
                result = next(wifi)
                advance(min(command_seconds, seconds))
                return result
            # A slow successful renderer must not move the measurement timestamp.
            advance(min(20,seconds))
            return pisched.Result(0,'rendered')
        env = {'PISCHED_PAGE': json.dumps(self.page),'PISCHED_HTTP_LATENCY_MS':'12.5'}
        if task_seconds is not None:
            env['PISCHED_TASK_BUDGET_SEC'] = str(task_seconds)
        with patch.object(c.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(c.time,'sleep',side_effect=advance), patch.object(c,'datetime',ClockDateTime), \
             patch.object(pisched.PiSugarClient,'cmd',side_effect=socket_command) as sock, \
             patch.object(c,'run_command',side_effect=command) as run, patch.dict(os.environ,env):
            code = c.collect(self.cfg,'event-retry')
        return code,pisched.read_json(self.cfg['metrics_file']),sock.call_args_list,run.call_args_list,clock[0]

    def test_late_numeric_recovery_uses_one_attempt_limit_and_actual_sample_time(self):
        self.cfg['pisugar']['max_attempts'] = 5
        responses = iter(['battery: I2C not connected','battery: I/O error','battery: nan',
                          'battery: -1','battery: 55','battery_v: 3.7','temperature: 30'])
        code,state,sockets,commands,_ = self.timed_case(responses,command_seconds=2)
        self.assertEqual(code,0)
        self.assertEqual(state['values']['battery_percent'],55)
        self.assertEqual(len([call for call in sockets if call.args[0] == 'get battery']),5)
        self.assertEqual(state['timestamp'],'2026-10-23T12:00:14+00:00')
        self.assertEqual(commands[-1].args[0],[self.cfg['display']['path']])
        self.assertEqual(commands[-1].args[1],self.cfg['display']['timeout_sec'])

    def test_permanently_invalid_number_stops_and_preserves_other_measurements(self):
        self.cfg['pisugar']['max_attempts'] = 5
        responses = iter(['battery: nan']*5 + ['battery_v: 3.7','temperature: 30'])
        code,state,sockets,commands,_ = self.timed_case(responses)
        self.assertEqual(code,1)
        self.assertIsNone(state['values']['battery_percent'])
        self.assertEqual(state['values']['battery_voltage'],3.7)
        self.assertEqual(len(sockets),7)
        self.assertEqual(commands[-1].kwargs['env']['PISCHED_BATTERY'],'')

    def test_transient_wifi_command_and_missing_signal_recover(self):
        results = [pisched.Result(1,'not ready'),pisched.Result(0,''),
                   pisched.Result(0,'Signal level=-45 dBm')]
        code,state,_,commands,_ = self.timed_case(
            iter(['battery: 55','battery_v: 3.7','temperature: 30']),wifi_results=results)
        self.assertEqual(code,0)
        self.assertEqual(state['values']['wifi_rssi'],-45)
        self.assertEqual(sum(call.args[0][0] == 'iwconfig' for call in commands),3)
        self.assertEqual(sum(call.args[0] == [self.cfg['display']['path']] for call in commands),1)

    def test_short_parent_budget_limits_metrics_before_render_reservation(self):
        self.cfg['pisugar']['max_attempts'] = 5
        # Parent grants 50s: collector has 48s, of which 45s belongs to display.
        code,state,sockets,commands,elapsed = self.timed_case(
            iter(['battery: I2C not connected']*5),task_seconds=50,command_seconds=2)
        self.assertEqual(code,1)
        self.assertEqual(len(sockets),1)
        self.assertTrue(all(state['values'][key] is None for key in
                            ('battery_percent','battery_voltage','temperature','wifi_rssi')))
        self.assertEqual(len(commands),1)
        self.assertEqual(commands[0].args[0],[self.cfg['display']['path']])
        self.assertEqual(commands[0].args[1],45)
        self.assertLess(elapsed,50)

    def test_wifi_retries_share_six_second_cap(self):
        results = [pisched.Result(1,'not ready')]*3
        code,state,_,commands,elapsed = self.timed_case(
            iter(['battery: 55','battery_v: 3.7','temperature: 30']),
            wifi_results=results,command_seconds=3)
        self.assertEqual(code,1)
        self.assertIsNone(state['values']['wifi_rssi'])
        wifi = [call for call in commands if call.args[0][0] == 'iwconfig']
        self.assertEqual(len(wifi),2)
        self.assertLess(wifi[-1].args[1],3)
        self.assertEqual(commands[-1].args[1],45)
        self.assertLessEqual(elapsed,35)

    def test_exhausted_parent_budget_does_not_launch_hardware_commands(self):
        code,state,sockets,commands,_ = self.timed_case(iter(()),task_seconds=1)
        self.assertEqual(code,1)
        self.assertEqual(sockets,[])
        self.assertEqual(commands,[])
        self.assertNotIn('display_returncode',state)
