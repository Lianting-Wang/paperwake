"""Failure-path regression tests: every device side effect is replaced, never main()."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from project_config import load_scheduler
s = load_scheduler()


class ScheduleTests(unittest.TestCase):
    def test_spring_gap_is_skipped(self):
        zone = ZoneInfo('America/New_York')
        now = datetime(2026, 3, 8, 1, 59, tzinfo=zone)
        result = s.compute_next_run(now, ['02:30'])
        self.assertEqual(result, datetime(2026, 3, 9, 2, 30, tzinfo=zone))

    def test_fall_repeated_time_runs_only_at_first_occurrence(self):
        zone = ZoneInfo('America/New_York')
        for fold in (0, 1):
            now = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=fold)
            result = s.compute_next_run(now, ['01:30'])
            self.assertEqual(result, datetime(2026, 11, 2, 1, 30, tzinfo=zone))

    def test_after_task_and_shutdown_margin_choose_future_alarm(self):
        cfg = s.validate_config({'schedule': ['12:00', '16:00']})
        client = Mock()
        now = datetime(2026, 10, 22, 16, 0, 15, tzinfo=timezone.utc)
        client.get.side_effect = ['2000-01-01T16:00:00+00:00', '127', 'true']
        state = {'time_trusted': True}
        # Use a datetime subclass so timezone arguments still behave normally.
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)
        with patch.object(s, 'datetime', Clock), patch.object(s, 'rtc_datetime', return_value=now):
            # 12:00 EDT was just passed; next alarm is 16:00 EDT = 20:00 UTC.
            client.get.side_effect = ['2000-01-01T20:00:00+00:00', '127', 'true']
            s.program_alarm(cfg, client, state)
        self.assertTrue(state['alarm_confirmed'])
        self.assertIn('16:00:00-04:00', state['next_run'])

    def test_alarm_requires_enabled_and_matching_repeat(self):
        cfg = s.validate_config({'schedule': ['23:59']})
        client = Mock()
        state = {'time_trusted': True}
        next_dt = s.compute_next_run(datetime.now(ZoneInfo(cfg['timezone'])) + timedelta(seconds=60), cfg['schedule'])
        client.get.side_effect = [next_dt.isoformat(), '127', 'false']
        with patch.object(s, 'rtc_datetime', return_value=datetime.now(timezone.utc)):
            with self.assertRaisesRegex(RuntimeError, 'enabled'):
                s.program_alarm(cfg, client, state)


    def test_shutdown_margin_uses_elapsed_seconds_across_fall_back(self):
        zone = ZoneInfo('America/New_York')
        cases = [
            (datetime(2026, 11, 1, 1, 59, 30, tzinfo=zone, fold=0), '02:00',
             datetime(2026, 11, 1, 2, 0, tzinfo=zone), 127),
            (datetime(2026, 11, 1, 1, 0, 30, tzinfo=zone, fold=1), '01:30',
             datetime(2026, 11, 2, 1, 30, tzinfo=zone), 2),
        ]
        for now, slot, expected, repeat in cases:
            with self.subTest(now=now.isoformat()):
                class Clock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return now.astimezone(tz) if tz else now.replace(tzinfo=None)
                cfg = s.validate_config({'schedule': [slot]})
                client = Mock()
                client.get.side_effect = [expected.isoformat(), str(repeat), 'true']
                state = {'time_trusted': True}
                with patch.object(s, 'datetime', Clock), patch.object(s, 'rtc_datetime', return_value=now):
                    s.program_alarm(cfg, client, state)
                self.assertEqual(state['next_run'], expected.isoformat())
                self.assertTrue(state['alarm_confirmed'])


class ConfigTests(unittest.TestCase):
    def test_boolean_string_and_unbounded_task_rejected_by_runtime(self):
        for raw in ({'shutdown': {'shutdown_even_if_alarm_set_failed': 'false'}},
                    {'task': {'timeout_sec': 0}}, {'notification': {'max_attempts': 999}},
                    {'runtime': {'max_seconds': 20}}, {'repeat_mask': 1},
                    {'notification': {'command': '/bin/true'}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                s.validate_config(dict(schedule=['08:00'], **raw))

    def test_timeout_types_are_rejected_as_configuration_errors(self):
        for value in (None, '5', False, 2.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                s.validate_config({'schedule':['08:00'], 'task':{'timeout_sec':value}})

    def test_alarm_margin_must_cover_failure_cleanup(self):
        with self.assertRaisesRegex(ValueError, 'shutdown_margin_seconds'):
            s.validate_config({'schedule':['08:00'], 'runtime':{'shutdown_margin_seconds':1}})
        with self.assertRaisesRegex(ValueError, 'shutdown_margin_seconds'):
            s.validate_config({'schedule':['08:00'], 'notification':{'total_timeout_sec':80}})
        cfg = s.validate_config({'schedule':['08:00']})
        cfg['runtime']['shutdown_margin_seconds'] = s.cleanup_reserve(cfg)
        self.assertEqual(s.validate_config(cfg)['runtime']['shutdown_margin_seconds'], s.cleanup_reserve(cfg))


class ProcessTests(unittest.TestCase):
    def test_silent_task_deadline(self):
        start = time.monotonic()
        result = s.run_command([sys.executable, '-c', 'import time; time.sleep(10)'], 1.3)
        self.assertEqual(result.returncode, 124)
        self.assertLess(time.monotonic() - start, 1.8)

    def test_child_keeps_stdout_but_parent_exit_does_not_block(self):
        code = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(10)"]); print("complete")'
        start = time.monotonic()
        result = s.run_command([sys.executable, '-c', code], 2)
        self.assertEqual(result.returncode, 0)
        self.assertIn('complete', result.output)
        self.assertLess(time.monotonic() - start, 1.5)

    def test_ignoring_term_and_grandchild_are_killed(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = str(Path(folder) / 'escaped')
            code = ('import subprocess,sys,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                    'subprocess.Popen([sys.executable,"-c",'
                    f'"import time,pathlib; time.sleep(1.7); pathlib.Path({marker!r}).touch()"]); time.sleep(10)')
            result = s.run_command([sys.executable, '-c', code], 1.3)
            self.assertEqual(result.returncode, 124)
            time.sleep(1.2)
            self.assertFalse(Path(marker).exists())


class TimeTests(unittest.TestCase):
    def test_wait_ignores_wall_clock_jump(self):
        with patch.object(s.time, 'time', side_effect=AssertionError('wall clock used')), \
             patch.object(s, 'time_synchronized', side_effect=[False, True]), \
             patch.object(s.time, 'sleep'):
            self.assertTrue(s.wait_for_time_sync(None, 10))

    def test_synchronized_clock_never_restored_from_rtc(self):
        cfg = s.validate_config({'schedule': ['08:00']})
        client = Mock()
        state = {}
        with patch.object(s, 'wait_for_time_sync', return_value=True), patch.object(s, 'rtc_datetime', return_value=datetime.now(timezone.utc)):
            s.prepare_time(cfg, client, state, {}, s.Budget(20))
        client.done.assert_called_once_with('rtc_pi2rtc')
        self.assertTrue(state['time_trusted'])

    def test_old_rtc_cannot_overwrite_system_clock(self):
        cfg = s.validate_config({'schedule': ['08:00']})
        client = Mock()
        with patch.object(s, 'wait_for_time_sync', return_value=False), patch.object(s, 'rtc_datetime', return_value=datetime(2020, 1, 1, tzinfo=timezone.utc)):
            with self.assertRaisesRegex(RuntimeError, 'trusted range'):
                s.prepare_time(cfg, client, {}, {}, s.Budget(20))
        client.done.assert_not_called()

    def test_rtc_error_response_propagates_after_ntp(self):
        cfg = s.validate_config({'schedule': ['08:00']})
        client = Mock()
        client.done.side_effect = RuntimeError('I/O error')
        with patch.object(s, 'wait_for_time_sync', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'I/O'):
                s.prepare_time(cfg, client, {}, {}, s.Budget(20))


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = s.validate_config({'schedule': ['08:00'], 'notification': {'command': ['/test/notify'], 'backoff_sec': 0}})
        self.state = {'event_id': 'one-event', 'failures': [{'stage': 'alarm', 'message': 'I/O'}]}

    def test_unconfigured_is_not_success(self):
        self.cfg['notification']['command'] = []
        self.assertEqual(s.notify_failure(self.cfg, self.state, s.Budget(20))['status'], 'not_configured')

    def test_retry_then_success_reuses_event(self):
        with patch.object(s, 'run_command', side_effect=[s.Result(1), s.Result(0)]) as run:
            result = s.notify_failure(self.cfg, self.state, s.Budget(20))
        self.assertEqual((result['status'], result['attempts']), ('accepted', 2))
        self.assertEqual(run.call_args_list[0].kwargs['input_data'], run.call_args_list[1].kwargs['input_data'])

    def test_failed_attempts_stop_at_limit(self):
        with patch.object(s, 'run_command', return_value=s.Result(1)) as run:
            result = s.notify_failure(self.cfg, self.state, s.Budget(20))
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(run.call_count, 3)

    def test_total_budget_and_cleanup_are_included(self):
        clock = [0.0]
        def hang(*args, **kwargs):
            clock[0] += args[1]
            return s.Result(124)
        self.cfg['notification'].update(timeout_sec=5, total_timeout_sec=6)
        with patch.object(s.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(s, 'run_command', side_effect=hang):
            result = s.notify_failure(self.cfg, self.state, s.Budget(6))
        self.assertEqual(result['attempts'], 1)
        self.assertLessEqual(clock[0], 6)

    def test_notification_spawn_failure_and_low_battery(self):
        with patch.object(s, 'run_command', side_effect=FileNotFoundError) as run:
            self.assertEqual(s.notify_failure(self.cfg, self.state, s.Budget(20))['attempts'], 3)
            self.state['low_battery'] = True
            self.assertEqual(s.notify_failure(self.cfg, self.state, s.Budget(20))['attempts'], 0)
        self.assertEqual(run.call_count, 3)


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.cfg = s.validate_config({'schedule': ['08:00'], 'state_file': str(folder/'state.json'),
                                     'metrics_file': str(folder/'metrics.json'), 'outbox_file': str(folder/'outbox.json'), 'log_file': str(folder/'run.log')})

    def effects(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        mocks = {}
        for name in ('wait_for_pisugar', 'prepare_time', 'program_alarm'):
            mocks[name] = stack.enter_context(patch.object(s, name))
        mocks['client'] = stack.enter_context(patch.object(s, 'PiSugarClient'))
        mocks['client'].return_value.get.return_value = '55'
        mocks['server'] = stack.enter_context(patch.object(s, 'ServerClient'))
        mocks['server'].return_value.check_in.return_value = {'debug': False, 'content_version': 1,
            'display': {'title': 'PI STATUS', 'status': 'READY', 'rows': []}}
        mocks['server'].return_value.last_latency_ms = 4
        mocks['server'].return_value.cfg = self.cfg['server']
        mocks['server'].return_value.report.return_value = {'accepted': True}
        mocks['task'] = stack.enter_context(patch.object(s, 'run_task', return_value=0))
        mocks['shutdown'] = stack.enter_context(patch.object(s, 'request_shutdown', return_value={'status':'requested'}))
        # No notification or display command may reach the OS from cycle tests.
        mocks['command'] = stack.enter_context(patch.object(s, 'run_command', side_effect=AssertionError('unexpected command')))
        return mocks

    def test_early_health_failure_still_runs_task_and_shuts_down(self):
        mocks = self.effects()
        mocks['wait_for_pisugar'].side_effect = RuntimeError('not ready')
        self.assertEqual(s.run_cycle(self.cfg), 1)
        mocks['task'].assert_called_once()
        mocks['shutdown'].assert_called_once()
        state = s.read_json(self.cfg['state_file'])
        self.assertEqual(state['failures'][0]['stage'], 'pisugar')
        self.assertEqual(state['shutdown']['status'], 'requested')

    def test_task_launch_error_still_attempts_alarm_and_shutdown(self):
        mocks = self.effects()
        mocks['task'].side_effect = FileNotFoundError('task missing')
        self.assertEqual(s.run_cycle(self.cfg), 1)
        mocks['program_alarm'].assert_called_once()
        mocks['shutdown'].assert_called_once()

    def test_bad_configuration_initial_error_skips_tasks_but_shuts_down(self):
        mocks = self.effects()
        self.assertEqual(s.run_cycle(self.cfg, 'bad JSON'), 1)
        mocks['task'].assert_not_called()
        mocks['shutdown'].assert_called_once()

    def test_storage_notification_and_error_display_failure_cannot_skip_shutdown(self):
        mocks = self.effects()
        self.cfg['failure_display']['command'] = ['/test/display']
        mocks['program_alarm'].side_effect = RuntimeError('alarm I/O')
        with patch.object(s, 'atomic_json', side_effect=OSError('disk full')), \
             patch.object(s, 'notify_failure', side_effect=RuntimeError('notification broke')):
            self.assertEqual(s.run_cycle(self.cfg), 1)
        mocks['shutdown'].assert_called_once()

    def test_cycle_timestamps_follow_trusted_clock_and_include_cleanup(self):
        mocks = self.effects()
        elapsed = [0.0]
        corrected = [False]
        trusted_start = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                start = trusted_start if corrected[0] else trusted_start - timedelta(minutes=3)
                value = start + timedelta(seconds=elapsed[0])
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        def prepare(cfg, client, state, previous, budget):
            elapsed[0] = 8
            corrected[0] = True
            state.update(time_trusted=True, time_source='ntp')
        def task(*args, **kwargs):
            elapsed[0] = 11
            return 0
        def notify(*args):
            elapsed[0] = 14
            return {'status': 'not_needed', 'attempts': 0}
        def shutdown(*args):
            elapsed[0] = 15
            return {'status': 'requested'}
        mocks['prepare_time'].side_effect = prepare
        mocks['task'].side_effect = task
        mocks['shutdown'].side_effect = shutdown
        with patch.object(s, 'datetime', Clock), patch.object(s.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(s, 'notify_failure', side_effect=notify):
            self.assertEqual(s.run_cycle(self.cfg), 0)
        state = s.read_json(self.cfg['state_file'])
        self.assertEqual(datetime.fromisoformat(state['started_at']), trusted_start)
        self.assertEqual(datetime.fromisoformat(state['finished_at']), trusted_start + timedelta(seconds=15))
        self.assertEqual(state['elapsed_seconds'], 15)

    def test_low_battery_skips_task(self):
        mocks = self.effects()
        mocks['client'].return_value.get.return_value = '5'
        self.assertEqual(s.run_cycle(self.cfg), 1)
        mocks['task'].assert_not_called()
        mocks['shutdown'].assert_called_once()

    def test_state_records_recovery_and_retains_last_failure(self):
        self.effects()
        s.atomic_json(self.cfg['state_file'], {'last_failure': {'event_id': 'old'}})
        self.assertEqual(s.run_cycle(self.cfg), 0)
        state = s.read_json(self.cfg['state_file'])
        self.assertTrue(state['recovered'])
        self.assertEqual(state['last_failure']['event_id'], 'old')

    def test_shutdown_failures_return_failure(self):
        mocks = self.effects()
        mocks['shutdown'].return_value = {'status': 'failed'}
        self.assertEqual(s.run_cycle(self.cfg), 1)
        state = s.read_json(self.cfg['state_file'])
        self.assertIn('shutdown', [f['stage'] for f in state['failures']])
        self.assertEqual(state['last_failure']['event_id'], state['event_id'])
        self.assertFalse(state['recovered'])
        self.assertEqual(state['notification']['status'], 'not_configured')

    def test_shutdown_failure_updates_last_failure_and_cancels_recovery(self):
        mocks = self.effects()
        s.atomic_json(self.cfg['state_file'], {'last_failure': {'event_id': 'old'}})
        mocks['shutdown'].return_value = {'status': 'failed'}
        self.assertEqual(s.run_cycle(self.cfg), 1)
        state = s.read_json(self.cfg['state_file'])
        self.assertFalse(state['recovered'])
        self.assertEqual(state['last_failure']['event_id'], state['event_id'])
        self.assertIn('shutdown', [item['stage'] for item in state['last_failure']['failures']])

    def test_first_failure_during_shutdown_uses_original_notification_deadline(self):
        mocks = self.effects()
        clock = [0.0]
        def shutdown(cfg, state, budget):
            clock[0] += 5
            return {'status': 'failed'}
        mocks['shutdown'].side_effect = shutdown
        self.cfg['notification']['command'] = ['/test/notify']
        entries = []
        def notify_result(cfg, state, budget):
            entries.append((state['notification']['status'], budget.deadline))
            return ({'status': 'not_needed', 'attempts': 0} if len(entries) == 1
                    else {'status': 'accepted', 'attempts': 1})
        with patch.object(s.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(s, 'notify_failure', side_effect=notify_result) as notify:
            self.assertEqual(s.run_cycle(self.cfg), 1)
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(entries, [('pending', 20), ('pending', 20)])
        state = s.read_json(self.cfg['state_file'])
        self.assertEqual(state['last_failure']['notification']['status'], 'accepted')
        self.assertFalse(state['recovered'])

    def test_shutdown_failure_does_not_restart_consumed_notification_attempts(self):
        mocks = self.effects()
        mocks['task'].return_value = 1
        mocks['shutdown'].return_value = {'status': 'failed'}
        self.cfg['notification']['command'] = ['/test/notify']
        with patch.object(s, 'notify_failure', return_value={'status':'failed', 'attempts':3}) as notify:
            self.assertEqual(s.run_cycle(self.cfg), 1)
        notify.assert_called_once()
        self.assertEqual(s.read_json(self.cfg['state_file'])['notification']['attempts'], 3)

    def test_debug_overrides_shutdown_and_normal_mode_retries(self):
        with patch.object(s.os.path, 'exists', return_value=True), patch.object(s, 'run_command') as command:
            self.assertEqual(s.request_shutdown(self.cfg, {}, s.Budget(20))['status'], 'debug')
            command.assert_not_called()
        with patch.object(s.os.path, 'exists', return_value=False), patch.object(s, 'run_command', side_effect=[s.Result(1), s.Result(0)]) as command:
            result = s.request_shutdown(self.cfg, {}, s.Budget(20))
            self.assertEqual(result, {'status':'requested','attempts':2,'returncode':0})


class PiSugarProtocolTests(unittest.TestCase):
    def setUp(self):
        self.cfg=s.validate_config({'schedule':['08:00']})['pisugar']
        self.cfg['retry_delay_sec']=0
        self.client=s.PiSugarClient(self.cfg,s.Budget(20))

    def test_intermittent_write_is_retried(self):
        with patch.object(self.client,'cmd',side_effect=['rtc_pi2rtc: I/O error (os error 5)', 'rtc_pi2rtc: done']) as cmd:
            self.client.done('rtc_pi2rtc')
        self.assertEqual(cmd.call_count,2)

    def test_partial_setter_is_repeated_with_same_target(self):
        command='rtc_alarm_set 2026-10-22T04:00:00.000-04:00 127'
        with patch.object(self.client,'cmd',side_effect=['rtc_alarm_set: I/O error','rtc_alarm_set: done']) as cmd:
            self.client.done(command)
        self.assertEqual([args.args[0] for args in cmd.call_args_list],[command,command])

    def test_repeat_failure_stops_at_max_attempts(self):
        with patch.object(self.client,'cmd',return_value='rtc_pi2rtc: I/O error') as cmd:
            with self.assertRaises(RuntimeError):self.client.done('rtc_pi2rtc')
        self.assertEqual(cmd.call_count,3)

    def test_read_error_is_not_measurement(self):
        with patch.object(self.client,'cmd',side_effect=['battery: I/O error', 'battery: 55']):
            self.assertEqual(self.client.get('battery'),'55')

    def test_socket_fragments_and_response_limit(self):
        with patch.object(s.socket,'socket') as socket_factory:
            sock=socket_factory.return_value.__enter__.return_value
            sock.recv.side_effect=[b'battery: ',b'55\n']
            self.assertEqual(self.client.get('battery'),'55')
            sock.recv.side_effect=[b'x'*4097]
            with self.assertRaisesRegex(RuntimeError,'too large'):self.client.cmd('get battery')


class LogTests(unittest.TestCase):
    def test_bounded_rotation_retains_recent_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'run.log'
            for i in range(7):
                path.write_text(str(i)*10)
                s.rotate_log(path,max_bytes=8,backups=4)
            self.assertEqual(Path(str(path)+'.1').read_text(),'6'*10)
            self.assertEqual(Path(str(path)+'.4').read_text(),'3'*10)
            self.assertFalse(Path(str(path)+'.5').exists())


class TimeCommandStatusTests(unittest.TestCase):
    def test_failed_status_command_is_not_trusted_even_if_it_printed_yes(self):
        with patch.object(s,'run_command',return_value=s.Result(124,'yes\n')):
            self.assertFalse(s.time_synchronized())


class LongDSTAlarmTests(unittest.TestCase):
    def test_fall_25_hour_gap_restricts_alarm_to_next_utc_weekday(self):
        zone=ZoneInfo('America/New_York')
        now=datetime(2026,10,31,7,59,tzinfo=zone)
        # Include the shutdown margin: 08:00 today must be skipped.
        cfg=s.validate_config({'schedule':['08:00']})
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return now.astimezone(tz) if tz else now.replace(tzinfo=None)
        client=Mock()
        # Next event is Sunday 08:00 EST = 13:00 UTC, over 24h from Saturday.
        client.get.side_effect=['2000-01-01T13:00:00+00:00','1','true']
        state={'time_trusted':True}
        with patch.object(s,'datetime',Clock),patch.object(s,'rtc_datetime',return_value=now):
            s.program_alarm(cfg,client,state)
        self.assertTrue(state['alarm_confirmed'])
        self.assertEqual(state['alarm_repeat'],1)
        self.assertIn('2026-11-01T08:00:00.000-05:00 1',client.done.call_args.args[0])

    def test_spring_missing_daily_slot_restricts_target_day(self):
        zone=ZoneInfo('America/New_York')
        now=datetime(2026,3,7,2,30,tzinfo=zone)
        cfg=s.validate_config({'schedule':['02:30']})
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return now.astimezone(tz) if tz else now.replace(tzinfo=None)
        client=Mock();client.get.side_effect=['2000-01-01T06:30:00+00:00','2','true']
        state={'time_trusted':True}
        with patch.object(s,'datetime',Clock),patch.object(s,'rtc_datetime',return_value=now):
            s.program_alarm(cfg,client,state)
        self.assertTrue(state['alarm_confirmed'])
        self.assertEqual(state['alarm_repeat'],2)  # Monday UTC; skip Sunday entirely.


class ServerCycleTests(unittest.TestCase):
    setUp = CycleTests.setUp
    effects = CycleTests.effects

    def test_unreachable_server_skips_display_and_shuts_down(self):
        effects = self.effects()
        effects['server'].return_value.check_in.side_effect = RuntimeError('connection refused')
        s.run_cycle(self.cfg)
        effects['task'].assert_not_called()
        effects['shutdown'].assert_called_once()
        self.assertFalse(s.read_json(self.cfg['state_file'])['remote_debug'])

    def test_remote_debug_survives_invalid_content_and_report_failure(self):
        effects = self.effects()
        effects['server'].return_value.check_in.return_value = {'debug': True, 'content_version': 1,
            'display': {'title': 'invalid\n', 'status': 'READY', 'rows': []}}
        effects['server'].return_value.report.side_effect = RuntimeError('disconnected')
        # Exercise real policy rather than the shutdown substitute.
        effects['shutdown'].side_effect = lambda cfg, state, budget: (
            {'status': 'debug', 'source': 'remote'} if state['remote_debug'] else {'status': 'requested'})
        self.assertEqual(s.run_cycle(self.cfg), 10)
        effects['task'].assert_not_called()
        state = s.read_json(self.cfg['state_file'])
        self.assertTrue(state['remote_debug'])
        self.assertEqual(state['shutdown']['source'], 'remote')
        self.assertIn('server_report', [f['stage'] for f in state['failures']])

    def test_previous_remote_debug_is_never_restored(self):
        s.atomic_json(self.cfg['state_file'], {'remote_debug': True})
        effects = self.effects()
        effects['server'].return_value.check_in.side_effect = RuntimeError('offline')
        s.run_cycle(self.cfg)
        self.assertFalse(s.read_json(self.cfg['state_file'])['remote_debug'])

    def test_remote_policy_does_not_execute_shutdown(self):
        with patch.object(s.os.path, 'exists', return_value=False), patch.object(s, 'run_command') as run:
            self.assertEqual(s.request_shutdown(self.cfg, {'remote_debug': True}, s.Budget(10))['source'], 'remote')
        run.assert_not_called()

    def test_report_includes_alarm_and_hold_intent(self):
        effects = self.effects()
        effects['server'].return_value.check_in.return_value['debug'] = True
        effects['program_alarm'].side_effect = lambda cfg, client, state: state.update(
            next_run='2026-10-23T08:00:00-04:00', alarm_confirmed=True)
        s.run_cycle(self.cfg)
        report = effects['server'].return_value.report.call_args.args[0]
        self.assertTrue(report['alarm_confirmed'])
        self.assertTrue(report['debug_hold'])
        self.assertEqual(report['shutdown_intent'], 'hold')

    def test_systemd_accepts_only_completed_debug_cycle_code(self):
        unit = (Path(__file__).resolve().parents[1] / 'deploy/systemd/pisched.service').read_text()
        self.assertIn('SuccessExitStatus=10', unit)
        self.assertIn('SERVICE_RESULT', unit)
        self.assertNotIn('/run/remote', unit)


    def test_upload_error_is_durably_queued_as_outcome(self):
        effects = self.effects()
        from server_client import ServerError
        effects['server'].return_value.report.side_effect = ServerError('lost ACK')
        s.run_cycle(self.cfg)
        outbox = s.ReportOutbox(self.cfg['outbox_file'])
        outcomes = [r for r in outbox.reports if r.get('phase') == 'outcome']
        self.assertEqual(len(outcomes), 1)
        self.assertIn('server_report', [f['stage'] for f in outcomes[0]['failures']])

    def test_disconnected_cycle_skips_failure_display_hook(self):
        effects = self.effects()
        self.cfg['failure_display']['command'] = ['/test/display']
        effects['server'].return_value.check_in.side_effect = RuntimeError('offline')
        s.run_cycle(self.cfg)
        effects['command'].assert_not_called()

    def test_task_cannot_consume_alarm_reserve(self):
        effects = self.effects()
        clock = [0.0]
        def task(command, timeout, *args):
            clock[0] += timeout
            return 124
        effects['task'].side_effect = task
        remaining = []
        effects['program_alarm'].side_effect = lambda cfg, client, state: remaining.append(effects['client'].call_args.args[1].remaining())
        with patch.object(s.time, 'monotonic', side_effect=lambda: clock[0]):
            s.run_cycle(self.cfg)
        self.assertGreaterEqual(remaining[0], s.ALARM_RESERVE_SECONDS)


    def test_corrupt_outbox_preserved_but_live_fault_reported(self):
        Path(self.cfg['outbox_file']).write_text('{broken')
        effects = self.effects()
        s.run_cycle(self.cfg)
        self.assertEqual(Path(self.cfg['outbox_file']).read_text(), '{broken')
        report = effects['server'].return_value.report.call_args.args[0]
        self.assertIn('outbox', [f['stage'] for f in report['failures']])


    def test_unwritable_outbox_still_reports_live_fault(self):
        effects = self.effects()
        with patch.object(s.ReportOutbox, '_save', side_effect=OSError('disk full')):
            s.run_cycle(self.cfg)
        report = effects['server'].return_value.report.call_args.args[0]
        self.assertIn('outbox', [f['stage'] for f in report['failures']])
