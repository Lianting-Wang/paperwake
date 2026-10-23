"""Battery estimates use retained reports, without hardware or live data."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.server.battery import estimate_battery


class BatteryEstimateTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE events (device_id TEXT, event_id TEXT, phase TEXT, '
                        'received_at TEXT, payload TEXT, PRIMARY KEY(device_id,event_id,phase))')
        self.db.execute('CREATE INDEX events_device_received ON events(device_id,received_at)')
        self.now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)

    def add(self, event, at, percent, phase='final', received=None, device='screen', **changes):
        payload = {'time_trusted': True, 'started_at': at.isoformat(),
                   'metrics': {'timestamp': at.isoformat(), 'values': {'battery_percent': percent}}}
        payload.update(changes)
        self.db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?)',
                        (device, event, phase, (received or self.now).isoformat(), json.dumps(payload)))

    def data(self):
        return estimate_battery(self.db, 'screen', self.now)

    def series(self, hours, percentages):
        for index, (hour, percent) in enumerate(zip(hours, percentages)):
            self.add(f'sample-{index}', self.now - timedelta(hours=hour), percent)

    def test_discharge_rate_and_reserve_forecast_allow_for_age_of_latest_sample(self):
        self.series([26, 14, 2], [82, 79, 76])
        result = self.data()
        self.assertEqual(result, {
            'status': 'ready', 'daily_consumption_pp': 6, 'remaining_days': 10.9167,
            'estimated_until': (self.now + timedelta(days=11, hours=-2)).isoformat(),
            'reserve_percent': 10, 'window_days': 7, 'sample_count': 3,
            'span_hours': 24, 'observed_drop_pp': 6, 'latest_percent': 76,
            'latest_at': (self.now - timedelta(hours=2)).isoformat(),
        })
        json.dumps(result, allow_nan=False)

    def test_different_report_phases_and_out_of_order_backfills_are_one_sample(self):
        old = self.now - timedelta(hours=24)
        self.add('z-old', old, 90, phase='check-in', received=old)
        self.add('z-old', old, 88, phase='outcome')
        self.add('z-old', old, 89, phase='final', received=self.now + timedelta(minutes=1))
        self.add('a-new', self.now, 82)
        self.add('m-middle', self.now - timedelta(hours=12), 85)
        result = self.data()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['sample_count'], 3)
        self.assertEqual(result['daily_consumption_pp'], 6)
        self.assertEqual(result['latest_percent'], 82)

    def test_small_jitter_does_not_accumulate_false_consumption(self):
        self.series([24, 20, 16, 12, 8, 4, 0], [90, 89, 90, 88, 89, 87, 88])
        result = self.data()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['daily_consumption_pp'], 2)
        self.assertEqual(result['observed_drop_pp'], 2)
        self.assertEqual(result['sample_count'], 7)

    def test_flat_data_does_not_claim_zero_consumption_or_unlimited_life(self):
        for percentages in ([100, 100, 100], [90, 89, 90], [90, 89.5, 89.1]):
            with self.subTest(percentages=percentages):
                self.db.execute('DELETE FROM events')
                self.series([24, 12, 0], percentages)
                result = self.data()
                self.assertEqual(result['status'], 'flat')
                self.assertIsNone(result['daily_consumption_pp'])
                self.assertIsNone(result['remaining_days'])
                self.assertIsNone(result['estimated_until'])

    def test_recent_charge_resets_old_discharge_and_then_requires_new_evidence(self):
        self.series([48, 36, 24, 12, 6, 0], [90, 80, 70, 90, 89, 88])
        result = self.data()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['sample_count'], 3)
        self.assertEqual(result['span_hours'], 12)
        self.assertEqual(result['daily_consumption_pp'], 4)
        self.db.execute('DELETE FROM events')
        self.series([24, 12, 0], [90, 80, 82])
        result = self.data()
        self.assertEqual(result['status'], 'charging')
        self.assertEqual(result['sample_count'], 1)
        self.assertIsNone(result['daily_consumption_pp'])
        self.assertIsNone(result['remaining_days'])

    def test_charge_is_detected_across_several_small_upward_steps(self):
        self.series([24, 18, 12, 6, 0], [80, 81, 82, 83, 84])
        self.assertEqual(self.data()['status'], 'charging')
        self.assertIsNone(self.data()['daily_consumption_pp'])

    def test_large_gaps_cannot_be_used_as_hidden_charge_free_discharge(self):
        self.series([72, 60, 48, 0], [90, 85, 80, 70])
        result = self.data()
        self.assertEqual(result['status'], 'gap')
        self.assertEqual(result['sample_count'], 1)
        self.assertIsNone(result['remaining_days'])
        # An exact 24-hour interval is supported for devices reporting once a day.
        self.db.execute('DELETE FROM events')
        self.series([48, 24, 0], [90, 85, 80])
        self.assertEqual(self.data()['status'], 'ready')
        self.assertEqual(self.data()['daily_consumption_pp'], 5)

    def test_stale_observations_do_not_predict_current_runtime(self):
        self.series([72, 48, 25], [90, 85, 80])
        result = self.data()
        self.assertEqual(result['status'], 'stale')
        self.assertIsNone(result['daily_consumption_pp'])
        self.assertIsNone(result['estimated_until'])

    def test_duplicate_times_and_insufficient_span_cannot_satisfy_evidence_threshold(self):
        self.series([12, 12, 0], [90, 90, 80])
        result = self.data()
        self.assertEqual(result['status'], 'insufficient')
        self.assertEqual(result['sample_count'], 2)
        self.db.execute('DELETE FROM events')
        self.series([5, 2, 0], [90, 85, 80])
        self.assertEqual(self.data()['status'], 'insufficient')
        self.db.execute('DELETE FROM events')
        self.series([6, 3, 0], [90, 89, 88])
        self.assertEqual(self.data()['status'], 'ready')
        self.assertEqual(self.data()['daily_consumption_pp'], 8)

    def test_untrusted_clocks_bad_values_other_devices_and_old_samples_are_excluded(self):
        self.series([24, 12, 0], [90, 85, 80])
        self.add('outside-window', self.now - timedelta(days=8), 100)
        self.add('wrong-device', self.now, 0, device='another')
        earlier = self.now - timedelta(minutes=1)
        self.add('untrusted', earlier, 1, received=earlier, time_trusted=False)
        self.add('fake-trust', earlier, 2, received=earlier, time_trusted='true')
        self.add('future', self.now + timedelta(hours=1), 3, received=earlier)
        self.add('naive', earlier, 4, received=earlier, started_at='2026-10-22T11:00:00')
        self.add('bad-start', earlier, 5, received=earlier, started_at='bad')
        self.add('bad-battery', self.now, True)
        self.add('too-high', self.now, 101)
        self.add('not-finite', self.now, float('inf'))
        result = self.data()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['sample_count'], 3)
        self.assertEqual(result['daily_consumption_pp'], 10)
        json.dumps(result, allow_nan=False)

    def test_new_unknown_clock_battery_blocks_older_trusted_prediction(self):
        self.series([25, 13, 1], [90, 85, 80])
        # Represent the older reports arriving at their actual sample times.
        for row in self.db.execute('SELECT event_id,payload FROM events').fetchall():
            at = json.loads(row['payload'])['started_at']
            self.db.execute('UPDATE events SET received_at=? WHERE event_id=?', (at, row['event_id']))
        self.assertEqual(self.data()['status'], 'ready')
        self.add('new-unknown-clock', self.now, 100, time_trusted=False)
        result = self.data()
        self.assertEqual(result['status'], 'insufficient')
        self.assertIsNone(result['daily_consumption_pp'])
        self.assertIsNone(result['remaining_days'])
        self.assertIsNone(result['estimated_until'])

    def test_actual_measurement_time_is_preferred_with_valid_started_time_fallback(self):
        self.series([24, 12, 0], [90, 85, 80])
        for event, hour in [('sample-0', 24), ('sample-1', 12), ('sample-2', 0)]:
            payload = json.loads(self.db.execute('SELECT payload FROM events WHERE event_id=?', (event,)).fetchone()[0])
            payload['started_at'] = (self.now - timedelta(hours=hour + 1)).isoformat()
            self.db.execute('UPDATE events SET payload=? WHERE event_id=?', (json.dumps(payload), event))
        self.assertEqual(self.data()['span_hours'], 24)
        # An unusable metric timestamp falls back to the verified cycle start.
        self.db.execute("UPDATE events SET payload=json_remove(payload, '$.metrics.timestamp')")
        result = self.data()
        self.assertEqual(result['latest_at'], (self.now - timedelta(hours=1)).isoformat())
        self.assertEqual(result['remaining_days'], 6.9583)

    def test_elapsed_days_use_utc_seconds_across_dst_boundary(self):
        local = ZoneInfo('America/New_York')
        first = datetime(2026, 10, 31, 12, tzinfo=local)
        middle = datetime(2026, 11, 1, 0, tzinfo=local)
        latest = datetime(2026, 11, 1, 12, tzinfo=local)
        self.now = latest.astimezone(timezone.utc)
        self.add('first', first, 90)
        self.add('middle', middle, 85)
        self.add('last', latest, 80)
        result = self.data()
        self.assertEqual(result['span_hours'], 25)
        self.assertEqual(result['daily_consumption_pp'], 9.6)

    def test_dense_estimate_uses_raw_samples_instead_of_chart_buckets(self):
        for index in range(1001):
            self.add(f'dense-{index:04}', self.now - timedelta(hours=144 * (1000 - index) / 1000),
                     100 - 12 * index / 1000)
        result = self.data()
        self.assertEqual(result['sample_count'], 1001)
        self.assertEqual(result['span_hours'], 144)
        self.assertEqual(result['daily_consumption_pp'], 2)

    def test_window_uses_measurement_time_even_if_cycle_started_outside_boundary(self):
        boundary = self.now - timedelta(days=7)
        for index in range(8):
            at = boundary + timedelta(days=index)
            self.add(f'day-{index}', at, 94 - 2 * index,
                     started_at=(at - timedelta(minutes=1)).isoformat())
        self.add('too-old', boundary - timedelta(seconds=1), 99)
        result = self.data()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['sample_count'], 8)
        self.assertEqual(result['span_hours'], 168)
        self.assertEqual(result['daily_consumption_pp'], 2)

    def test_reaching_the_reserve_never_returns_negative_runtime(self):
        self.series([24, 12, 0], [20, 15, 10])
        self.assertEqual(self.data()['remaining_days'], 0)
        self.assertEqual(self.data()['estimated_until'], self.now.isoformat())
        self.now += timedelta(hours=1)
        self.assertEqual(self.data()['remaining_days'], 0)

    def test_empty_history_transaction_state_and_temp_storage_are_preserved(self):
        self.assertEqual(self.data()['status'], 'insufficient')
        self.assertFalse(self.db.in_transaction)
        self.series([24, 12, 0], [90, 85, 80])
        self.assertTrue(self.db.in_transaction)
        self.data()
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 3)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sqlite_temp_master').fetchone()[0], 0)
        self.db.commit()
        self.data()
        self.assertFalse(self.db.in_transaction)
        with self.assertRaises(ValueError):
            estimate_battery(self.db, 'screen', self.now.replace(tzinfo=None))


if __name__ == '__main__':
    unittest.main()
