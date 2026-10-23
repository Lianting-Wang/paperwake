"""Offline regressions for bounded RTC recovery and persistent wake policies."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import test_scheduler as fixtures
from wake_plan import make_wake_plan

s = fixtures.s


def frozen_clock(current):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)
    return Clock


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = s.validate_config({'schedule': ['08:00']})

    def test_initialization_can_recover_after_old_fifteen_second_limit(self):
        elapsed = [0.0]
        def sleep(seconds):
            elapsed[0] += seconds
        def command(value):
            elapsed[0] += .1
            if value == 'get model':
                return 'model: PiSugar 3'
            return ('rtc_time: I2C not connected' if elapsed[0] < 20 else
                    'rtc_time: 2026-10-23T12:00:00+00:00')
        with patch.object(s.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(s.time, 'sleep', side_effect=sleep):
            client = s.PiSugarClient(self.cfg['pisugar'], s.Budget(60))
            original = client.budget
            with patch.object(client, 'cmd', side_effect=command):
                s.wait_for_pisugar(client)
            self.assertIs(client.budget, original)
        self.assertGreater(elapsed[0], 15)
        self.assertLess(elapsed[0], 45)

    def test_bad_measurements_share_five_command_limit(self):
        cfg = dict(self.cfg['pisugar'], retry_delay_sec=0)
        client = s.PiSugarClient(cfg, s.Budget(20))
        def numeric(value):
            number = float(value)
            if not 0 <= number <= 100:
                raise ValueError('outside battery range')
            return number
        with patch.object(client, 'cmd', side_effect=[
                'battery: I2C not connected', 'battery: NaN', 'battery: 101',
                'battery: unavailable', 'battery: 45']):
            self.assertEqual(client.get('battery', validator=numeric), 45)

    def test_partial_alarm_write_reinitializes_and_resynchronizes(self):
        now = datetime(2026, 10, 23, 11, tzinfo=timezone.utc)
        target = datetime(2026, 10, 23, 8, tzinfo=ZoneInfo('America/New_York'))
        client = Mock()
        client.done.side_effect = [RuntimeError('partial alarm I/O'), None]
        client.get.side_effect = [target.isoformat(), '127', 'true']
        state = {'time_trusted': True}
        with patch.object(s, 'datetime', frozen_clock(now)), \
             patch.object(s, 'rtc_datetime', return_value=now), \
             patch.object(s, 'wait_for_pisugar') as ready, \
             patch.object(s, 'prepare_time') as sync, patch.object(s.Budget, 'sleep'):
            s.recover_alarm(self.cfg, client, state, {}, s.Budget(60), prepared=True)
        self.assertEqual(ready.call_count, 1)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(client.done.call_count, 2)
        self.assertEqual(client.done.call_args_list[0], client.done.call_args_list[1])
        self.assertTrue(state['alarm_confirmed'])
        self.assertEqual(state['pisugar_recovery']['attempts'], 2)

    def test_mismatching_readback_repeats_whole_alarm_transaction(self):
        now = datetime(2026, 10, 23, 11, tzinfo=timezone.utc)
        target = datetime(2026, 10, 23, 8, tzinfo=ZoneInfo('America/New_York'))
        client = Mock()
        client.get.side_effect = [target.isoformat(), '127', 'false', target.isoformat(), '127', 'true']
        state = {'time_trusted': True}
        with patch.object(s, 'datetime', frozen_clock(now)), \
             patch.object(s, 'rtc_datetime', return_value=now), \
             patch.object(s, 'wait_for_pisugar'), patch.object(s, 'prepare_time') as sync, \
             patch.object(s.Budget, 'sleep'):
            s.recover_alarm(self.cfg, client, state, {}, s.Budget(60), prepared=True)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(client.done.call_count, 2)
        self.assertTrue(state['alarm_confirmed'])

    def test_hard_failure_stops_at_work_deadline(self):
        elapsed = [0.0]
        state = {'time_trusted': False}
        client = Mock()
        def fail(client):
            elapsed[0] += min(45, client.budget.remaining())
            raise RuntimeError('I2C unavailable')
        with patch.object(s.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(s.time, 'sleep', side_effect=lambda value: elapsed.__setitem__(0, elapsed[0] + value)), \
             patch.object(s, 'wait_for_pisugar', side_effect=fail) as ready, \
             patch.object(s, 'prepare_time') as sync, patch.object(s, 'program_alarm') as alarm:
            budget = s.Budget(60)
            client.budget = budget
            with self.assertRaisesRegex(RuntimeError, 'cannot confirm'):
                s.recover_alarm(self.cfg, client, state, {}, budget)
        self.assertLessEqual(elapsed[0], 60)
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(state['pisugar_recovery']['status'], 'budget_exhausted')
        sync.assert_not_called()
        alarm.assert_not_called()

    def test_fast_hard_failures_stop_after_five_recovery_rounds(self):
        state = {'time_trusted': False}
        with patch.object(s, 'wait_for_pisugar', side_effect=RuntimeError('I2C unavailable')) as ready, \
             patch.object(s.Budget, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'cannot confirm'):
                s.recover_alarm(self.cfg, Mock(), state, {}, s.Budget(60))
        self.assertEqual(ready.call_count, 5)
        self.assertEqual(state['pisugar_recovery']['attempts'], 5)

    def test_notification_can_share_twenty_seconds_across_five_hanging_attempts(self):
        elapsed = [0.0]
        self.cfg['notification']['command'] = ['/test/notify']
        state = {'failures': [{'stage': 'alarm', 'message': 'I2C unavailable'}]}
        def hang(argv, seconds, **kwargs):
            elapsed[0] += seconds
            return s.Result(124)
        with patch.object(s.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(s.time, 'sleep', side_effect=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)), \
             patch.object(s, 'run_command', side_effect=hang):
            result = s.notify_failure(self.cfg, state, s.Budget(20))
        self.assertEqual(result['attempts'], 5)
        self.assertLessEqual(elapsed[0], 20)


class CachedWakeTests(unittest.TestCase):
    setUp = fixtures.CycleTests.setUp
    effects = fixtures.CycleTests.effects

    def plan(self, schedule=None, generated=None):
        return make_wake_plan({'timezone': 'America/New_York', 'schedule': schedule or ['09:00', '17:00']},
                              generated or datetime(2026, 10, 22, 12, tzinfo=timezone.utc), version=7)

    def trusted(self, effects):
        effects['prepare_time'].side_effect = lambda cfg, client, state, previous, budget: state.update(
            time_trusted=True, time_source='ntp')

    def test_initial_readiness_failure_gets_a_second_clock_recovery_chance(self):
        effects = self.effects()
        effects['wait_for_pisugar'].side_effect = [RuntimeError('startup I2C'), None]
        self.trusted(effects)
        effects['program_alarm'].side_effect = lambda cfg, client, state: state.update(
            alarm_confirmed=state['time_trusted'], next_run='2026-10-24T09:00:00-04:00')
        self.assertEqual(s.run_cycle(self.cfg), 1)
        effects['prepare_time'].assert_called_once()
        effects['task'].assert_called_once()
        effects['shutdown'].assert_called_once()
        state = s.read_json(self.cfg['state_file'])
        self.assertTrue(state['alarm_confirmed'])
        self.assertEqual(state['pisugar_recovery']['status'], 'confirmed')

    def test_hard_i2c_fault_exhausts_work_but_reserves_shutdown_budget(self):
        effects = self.effects()
        elapsed = [0.0]
        def unavailable(client):
            elapsed[0] += min(45, client.budget.remaining())
            raise RuntimeError('I2C unavailable')
        def task(command, seconds, log_file, env):
            self.assertEqual(float(env['PISCHED_TASK_BUDGET_SEC']), seconds)
            elapsed[0] += 35
            return 1
        effects['wait_for_pisugar'].side_effect = unavailable
        effects['task'].side_effect = task
        remaining = []
        effects['shutdown'].side_effect = lambda cfg, state, budget: (
            remaining.append(budget.remaining()) or {'status': 'requested'})
        with patch.object(s.time, 'monotonic', side_effect=lambda: elapsed[0]):
            self.assertEqual(s.run_cycle(self.cfg), 1)
        state = s.read_json(self.cfg['state_file'])
        self.assertFalse(state['alarm_confirmed'])
        self.assertEqual(state['shutdown']['status'], 'requested')
        self.assertGreaterEqual(remaining[0], s.cleanup_reserve(self.cfg))
        self.assertLessEqual(state['elapsed_seconds'], self.cfg['runtime']['max_seconds'])

    def test_failed_first_clock_setup_is_retried_before_alarm(self):
        effects = self.effects()
        def prepare(cfg, client, state, previous, budget):
            if effects['prepare_time'].call_count == 1:
                raise RuntimeError('RTC write failed')
            state.update(time_trusted=True, time_source='ntp')
        effects['prepare_time'].side_effect = prepare
        s.run_cycle(self.cfg)
        self.assertEqual(effects['prepare_time'].call_count, 2)
        self.assertTrue(s.read_json(self.cfg['state_file'])['time_trusted'])

    def test_download_is_persisted_and_server_seeding_uses_local_policy(self):
        effects = self.effects()
        self.trusted(effects)
        effects['server'].return_value.check_in.return_value['wake_plan'] = self.plan()
        s.run_cycle(self.cfg)
        self.assertEqual(s.load_wake_plan(self.cfg['wake_plan_file']), self.plan())
        alarm_cfg = effects['program_alarm'].call_args.args[0]
        self.assertEqual(alarm_cfg['schedule'], ['09:00', '17:00'])
        self.assertEqual(effects['server'].return_value.check_in.call_args.args[0]['wake_schedule'],
                         {'timezone': self.cfg['timezone'], 'schedule': self.cfg['schedule']})
        self.assertEqual(s.read_json(self.cfg['state_file'])['wake_plan']['source'], 'server')

    def test_offline_uses_cache_and_never_restores_debug(self):
        plan = self.plan()
        s.atomic_json(self.cfg['wake_plan_file'], plan)
        s.atomic_json(self.cfg['state_file'], {'remote_debug': True})
        effects = self.effects()
        self.trusted(effects)
        effects['server'].return_value.check_in.side_effect = RuntimeError('network unavailable')
        self.assertEqual(s.run_cycle(self.cfg), 1)
        state = s.read_json(self.cfg['state_file'])
        self.assertEqual(state['wake_plan']['source'], 'cache')
        self.assertEqual(state['wake_plan']['version'], 7)
        self.assertFalse(state['remote_debug'])
        self.assertEqual(effects['program_alarm'].call_args.args[0]['schedule'], plan['schedule'])
        effects['shutdown'].assert_called_once()
        effects['task'].assert_not_called()

    def test_corrupt_cache_falls_back_to_device_policy(self):
        Path(self.cfg['wake_plan_file']).write_text('{broken')
        effects = self.effects()
        effects['server'].return_value.check_in.side_effect = RuntimeError('offline')
        s.run_cycle(self.cfg)
        state = s.read_json(self.cfg['state_file'])
        self.assertEqual(state['wake_plan']['source'], 'local')
        self.assertIn('wake_plan_cache', [item['stage'] for item in state['failures']])
        self.assertIs(effects['program_alarm'].call_args.args[0], self.cfg)
        self.assertEqual(Path(self.cfg['wake_plan_file']).read_text(), '{broken')

    def test_deep_cache_parser_failure_still_falls_back_and_shuts_down(self):
        Path(self.cfg['wake_plan_file']).write_text('{}')
        effects = self.effects()
        effects['server'].return_value.check_in.side_effect = RuntimeError('offline')
        real_loads = s.json.loads
        def loads(value, *args, **kwargs):
            if value == '{}':
                raise RecursionError('JSON parser depth')
            return real_loads(value, *args, **kwargs)
        with patch.object(s.json, 'loads', side_effect=loads):
            self.assertEqual(s.run_cycle(self.cfg), 1)
        effects['shutdown'].assert_called_once()
        state = s.read_json(self.cfg['state_file'])
        self.assertEqual(state['wake_plan']['source'], 'local')
        self.assertIn('wake_plan_cache', [item['stage'] for item in state['failures']])

    def test_invalid_download_keeps_cache_and_current_remote_debug(self):
        old = self.plan()
        s.atomic_json(self.cfg['wake_plan_file'], old)
        effects = self.effects()
        effects['server'].return_value.check_in.return_value.update(debug=True, wake_plan={'debug': True})
        self.assertEqual(s.run_cycle(self.cfg), 10)
        state = s.read_json(self.cfg['state_file'])
        self.assertTrue(state['remote_debug'])
        self.assertEqual(state['wake_plan']['source'], 'cache')
        self.assertEqual(s.load_wake_plan(self.cfg['wake_plan_file']), old)
        effects['task'].assert_called_once()

    def test_unwritable_cache_preserves_old_file_and_uses_live_policy(self):
        old = self.plan()
        s.atomic_json(self.cfg['wake_plan_file'], old)
        new = self.plan(['10:00'])
        effects = self.effects()
        effects['server'].return_value.check_in.return_value.update(debug=True, wake_plan=new)
        real_write = s.atomic_json
        def write(path, value):
            if path == self.cfg['wake_plan_file']:
                raise OSError('disk full')
            return real_write(path, value)
        with patch.object(s, 'atomic_json', side_effect=write):
            self.assertEqual(s.run_cycle(self.cfg), 10)
        state = s.read_json(self.cfg['state_file'])
        self.assertEqual(s.load_wake_plan(self.cfg['wake_plan_file']), old)
        self.assertTrue(state['remote_debug'])
        self.assertFalse(state['wake_plan']['cached'])
        self.assertEqual(effects['program_alarm'].call_args.args[0]['schedule'], ['10:00'])

    def test_expired_absolute_dates_continue_policy_across_dst(self):
        plan = self.plan(['08:00'], datetime(2026, 10, 20, tzinfo=timezone.utc))
        now = datetime(2026, 10, 31, 7, 59, tzinfo=ZoneInfo('America/New_York'))
        cfg = dict(self.cfg, timezone=plan['timezone'], schedule=plan['schedule'], _wake_plan=plan)
        client = Mock()
        client.get.side_effect = ['2000-01-01T13:00:00+00:00', '1', 'true']
        state = {'time_trusted': True}
        with patch.object(s, 'datetime', frozen_clock(now)), patch.object(s, 'rtc_datetime', return_value=now):
            s.program_alarm(cfg, client, state)
            summary = s.wake_plan_summary(cfg, plan, 'cache', state, True)
        self.assertEqual(state['next_run'], '2026-11-01T08:00:00-05:00')
        self.assertEqual(state['alarm_repeat'], 1)
        self.assertTrue(summary['continued_from_policy'])
        self.assertEqual(summary['future_occurrences'][0], '2026-11-01T13:00:00+00:00')

    def test_future_server_clock_cannot_skip_policy_wakes_after_time_rewind(self):
        plan = self.plan(['08:00'], datetime(2026, 10, 30, tzinfo=timezone.utc))
        now = datetime(2026, 10, 23, 7, tzinfo=ZoneInfo('America/New_York'))
        cfg = dict(self.cfg, timezone=plan['timezone'], schedule=plan['schedule'], _wake_plan=plan)
        client = Mock()
        client.get.side_effect = ['2000-01-01T12:00:00+00:00', '127', 'true']
        state = {'time_trusted': True}
        with patch.object(s, 'datetime', frozen_clock(now)), patch.object(s, 'rtc_datetime', return_value=now):
            s.program_alarm(cfg, client, state)
            summary = s.wake_plan_summary(cfg, plan, 'cache', state, True)
        self.assertEqual(state['next_run'], '2026-10-23T08:00:00-04:00')
        self.assertEqual(summary['future_occurrences'][0], '2026-10-23T12:00:00+00:00')
        self.assertEqual(state['alarm_repeat'], 127)

    def test_untrusted_clock_does_not_claim_future_cached_occurrences(self):
        summary = s.wake_plan_summary(self.cfg, self.plan(), 'cache', {'time_trusted': False}, True)
        self.assertEqual(summary['future_occurrences'], [])
        self.assertEqual(summary['version'], 7)

    def test_dense_valid_cache_roundtrips_its_disk_encoding(self):
        schedule = [f'{hour:02d}:{minute:02d}' for hour in range(24) for minute in range(60)]
        plan = self.plan(schedule)
        # Pretty printing is a valid cache representation too, even when the
        # HTTP response's canonical compact encoding remains below 64 KiB.
        Path(self.cfg['wake_plan_file']).write_text(json.dumps(plan, indent=2))
        self.assertGreater(Path(self.cfg['wake_plan_file']).stat().st_size, 65536)
        self.assertEqual(s.load_wake_plan(self.cfg['wake_plan_file']), plan)

    def test_wake_cache_path_is_absolute_independent_and_no_debug_is_cached(self):
        for path in ('relative.json', '/var/lib/../state.json', self.cfg['state_file']):
            with self.subTest(path=path), self.assertRaises(ValueError):
                s.validate_config(dict(self.cfg, wake_plan_file=path))
        plan = self.plan()
        self.assertEqual(set(plan), {'version', 'timezone', 'schedule', 'generated_at', 'occurrences'})


if __name__ == '__main__':
    unittest.main()
