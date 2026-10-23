"""History analytics against SQLite, with no hardware or production records."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.server.history import build_history_data, merge_cycle


class HistoryDataTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE events (device_id TEXT, event_id TEXT, phase TEXT, '
                        'received_at TEXT, payload TEXT, PRIMARY KEY(device_id,event_id,phase))')
        self.db.execute('CREATE INDEX events_device_received ON events(device_id,received_at)')
        self.now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)

    def add(self, event, phase='final', received=None, device='screen', **payload):
        received = self.now if received is None else received
        self.db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?)',
                        (device, event, phase, received.isoformat(), json.dumps(payload)))

    def data(self, days=None):
        return build_history_data(self.db, 'screen', self.now, days)

    def test_merge_cycle_prefers_phase_over_arrival_and_preserves_nested_metrics(self):
        rows = [
            {'phase': 'outcome', 'payload': {'shutdown': {'status': 'failed'}, 'failures': ['shutdown']}},
            {'phase': 'check-in', 'payload': json.dumps({'battery_percent': 82, 'time_trusted': False})},
            {'phase': 'final', 'payload': {'metrics': {'values': {'battery_voltage': 4.1}},
                                          'shutdown': {'status': 'requesting', 'attempts': 1},
                                          'time_trusted': True, 'failures': []}},
        ]
        merged = merge_cycle(rows)
        self.assertEqual(merged['battery_percent'], 82)
        self.assertEqual(merged['metrics']['values']['battery_voltage'], 4.1)
        self.assertEqual(merged['shutdown'], {'status': 'failed', 'attempts': 1})
        self.assertEqual(merged['failures'], ['shutdown'])
        self.assertTrue(merged['time_trusted'])
        self.assertEqual(rows[2]['payload']['shutdown']['status'], 'requesting')

    def test_summary_and_curve_count_cycles_instead_of_report_phases(self):
        first = self.now - timedelta(days=2)
        second = self.now - timedelta(days=1)
        self.add('z-early', 'check-in', received=first, time_trusted=True,
                 started_at=first.isoformat(), battery_percent=82)
        self.add('z-early', 'final', received=first + timedelta(seconds=10), elapsed_seconds=10,
                 metrics={'values': {'battery_percent': 80, 'battery_voltage': 4.2,
                                     'temperature': 31, 'wifi_rssi': -45}},
                 server={'latency_ms': 7.3}, failures=['transient'])
        self.add('z-early', 'outcome', received=first + timedelta(seconds=20),
                 elapsed_seconds=11, failures=[])
        self.add('a-middle', 'check-in', received=second, started_at=second.isoformat(), time_trusted=True)
        self.add('a-middle', received=second + timedelta(seconds=20), elapsed_seconds=20,
                 metrics={'values': {'battery_percent': 75, 'battery_voltage': 4.0}}, failures=['alarm'])
        self.add('m-latest', 'check-in', battery_percent=74)
        self.add('different-device', device='other', battery_percent=1)
        result = self.data()
        self.assertEqual(result['summary'], {
            'cycles': 3, 'reports': 6, 'failure_cycles': 1, 'latest_battery_percent': 74,
            'battery_change_pp': -6, 'battery_change_hours': 48,
            'median_cycle_seconds': 15.5, 'latest_voltage': 4.0,
        })
        self.assertEqual(result['coverage'], {'start': first.isoformat(), 'end': self.now.isoformat()})
        self.assertEqual(result['bucket_seconds'], 0)
        self.assertEqual([point['battery_percent'] for point in result['series']], [80, 75, 74])
        self.assertEqual(result['series'][0]['temperature_c'], 31)
        self.assertEqual(result['series'][0]['http_latency_ms'], 7.3)
        self.assertEqual(result['series'][0]['elapsed_seconds'], 11)
        self.assertEqual(sum(point['failures'] for point in result['series']), 1)
        json.dumps(result, allow_nan=False)

    def test_backfill_uses_trusted_device_time_and_future_or_naive_clock_falls_back(self):
        old = self.now - timedelta(days=10)
        self.add('old-trusted', started_at=old.isoformat(), time_trusted=True, battery_percent=90)
        self.add('old-untrusted', started_at=old.isoformat(), time_trusted=False, battery_percent=80)
        self.add('future-trusted', started_at=(self.now + timedelta(days=365)).isoformat(),
                 time_trusted=True, battery_percent=70)
        self.add('naive-trusted', started_at='2026-10-01T10:00:00', time_trusted=True, battery_percent=60)
        self.add('invalid-trusted', started_at='not a date', time_trusted=True, battery_percent=50)
        self.add('invalid-trust-type', started_at=old.isoformat(), time_trusted='true', battery_percent=40)
        self.add('future-receipt', received=self.now + timedelta(days=1), battery_percent=30)
        result = self.data(7)
        self.assertEqual(result['summary']['cycles'], 5)
        self.assertEqual({point['at'] for point in result['series']}, {self.now.isoformat()})
        all_data = self.data()
        self.assertEqual(all_data['summary']['cycles'], 6)
        self.assertEqual(all_data['coverage']['start'], old.isoformat())

    def test_late_final_does_not_overwrite_outcome_clock_or_metrics(self):
        started = self.now - timedelta(hours=4)
        self.add('backfill', 'outcome', started_at=started.isoformat(), time_trusted=True,
                 metrics={'values': {'battery_percent': 70}}, failures=['shutdown'])
        self.add('backfill', 'final', received=self.now + timedelta(minutes=1),
                 started_at=(started + timedelta(minutes=1)).isoformat(), time_trusted=True,
                 metrics={'values': {'battery_percent': 75, 'battery_voltage': 4.1}}, failures=[])
        result = self.data()
        self.assertEqual(result['summary']['cycles'], 1)
        self.assertEqual(result['series'][0]['at'], started.isoformat())
        self.assertEqual(result['series'][0]['battery_percent'], 70)
        self.assertEqual(result['series'][0]['battery_voltage'], 4.1)
        self.assertEqual(result['summary']['failure_cycles'], 1)

    def test_range_includes_exact_boundary_and_only_selected_device(self):
        boundary = self.now - timedelta(days=3)
        self.add('boundary', received=boundary, battery_percent=80)
        self.add('outside', received=boundary - timedelta(microseconds=1), battery_percent=90)
        self.add('latest', battery_percent=75)
        self.add('other', device='other', battery_percent=0)
        self.assertEqual(self.data(3)['summary']['cycles'], 2)
        self.assertEqual(self.data('all')['summary']['cycles'], 3)
        for invalid in (0, 1, 6, 365, True, '7'):
            with self.assertRaises(ValueError):
                self.data(invalid)
        with self.assertRaises(ValueError):
            build_history_data(self.db, 'screen', self.now.replace(tzinfo=None), None)

    def test_recent_range_candidate_keeps_older_phases_for_clock_and_metrics(self):
        old = self.now - timedelta(days=10)
        # The recent final must not create a new cycle or pull this old trusted
        # check-in into the recent range merely because it arrived from the outbox.
        self.add('old-backfill', 'check-in', received=old, started_at=old.isoformat(),
                 time_trusted=True, battery_percent=88)
        self.add('old-backfill', 'final', received=self.now, elapsed_seconds=12)
        self.assertEqual(self.data(7)['summary']['cycles'], 0)
        result = self.data()
        self.assertEqual(result['series'][0]['at'], old.isoformat())
        self.assertEqual(result['series'][0]['battery_percent'], 88)
        self.assertEqual(result['summary']['reports'], 2)
        # At the exact range boundary, the older report is still selected for
        # merge and the final contributes its duration without duplicating cycles.
        self.db.execute('UPDATE events SET received_at=? WHERE phase=?',
                        ((self.now - timedelta(days=7)).isoformat(), 'check-in'))
        self.db.execute('UPDATE events SET payload=? WHERE phase=?',
                        (json.dumps({'time_trusted': True,
                                     'started_at': (self.now - timedelta(days=7)).isoformat(),
                                     'battery_percent': 88}), 'check-in'))
        result = self.data(7)
        self.assertEqual(result['summary']['cycles'], 1)
        self.assertEqual(result['summary']['reports'], 2)
        self.assertEqual(result['series'][0]['battery_percent'], 88)

    def test_missing_invalid_and_zero_measurements_are_distinguished(self):
        self.add('bad', metrics={'values': {'battery_percent': 101, 'battery_voltage': float('inf'),
                                          'temperature': True, 'wifi_rssi': 'bad',
                                          'http_latency_ms': -1}},
                 battery_percent=0, server={'latency_ms': 0}, elapsed_seconds=float('nan'))
        point = self.data()['series'][0]
        self.assertEqual(point['battery_percent'], 0)
        self.assertEqual(point['http_latency_ms'], 0)
        for key in ('battery_voltage', 'temperature_c', 'wifi_rssi', 'elapsed_seconds'):
            self.assertIsNone(point[key])
        self.assertIsNone(self.data()['summary']['battery_change_pp'])
        self.assertIsNone(self.data()['summary']['battery_change_hours'])
        self.db.execute('DELETE FROM events')
        self.add('zero', metrics={'values': {'battery_percent': 0, 'battery_voltage': 0,
                                           'temperature': 0, 'wifi_rssi': 0, 'http_latency_ms': 0}},
                 elapsed_seconds=0)
        point = self.data()['series'][0]
        for key in ('battery_percent', 'battery_voltage', 'temperature_c', 'wifi_rssi',
                    'http_latency_ms', 'elapsed_seconds'):
            self.assertEqual(point[key], 0)
        self.assertEqual(self.data()['summary']['median_cycle_seconds'], 0)

    def test_empty_or_corrupt_records_do_not_create_fake_metrics(self):
        empty = self.data()
        self.assertEqual(empty['series'], [])
        self.assertEqual(empty['summary']['cycles'], 0)
        self.assertEqual(empty['coverage'], {'start': None, 'end': None})
        self.add('broken-shape', metrics=[], battery_percent=False, elapsed_seconds='4')
        self.db.execute("INSERT INTO events VALUES ('screen','broken-json','final',?,?)",
                        (self.now.isoformat(), '{broken'))
        result = self.data()
        self.assertEqual(result['summary']['cycles'], 2)
        self.assertTrue(all(point['battery_percent'] is None for point in result['series']))
        json.dumps(result, allow_nan=False)

    def test_dense_history_has_bounded_mean_buckets_and_exact_summary(self):
        start = self.now - timedelta(hours=500)
        for index in range(501):
            at = start + timedelta(hours=index)
            self.add(f'cycle-{index:04}', received=at, started_at=at.isoformat(), time_trusted=True,
                     metrics={'values': {'battery_percent': 100 - index / 10,
                                         'battery_voltage': None}}, elapsed_seconds=index + 1,
                     failures=['task'] if index % 10 == 0 else [])
        result = self.data(30)
        self.assertGreater(result['bucket_seconds'], 0)
        self.assertLessEqual(len(result['series']), 400)
        self.assertEqual(sum(point['cycles'] for point in result['series']), 501)
        self.assertEqual(sum(point['failures'] for point in result['series']), 51)
        self.assertEqual(result['summary']['cycles'], 501)
        self.assertEqual(result['summary']['median_cycle_seconds'], 251)
        self.assertEqual(result['summary']['latest_battery_percent'], 50)
        self.assertEqual(result['summary']['battery_change_pp'], -50)
        self.assertEqual(result['summary']['battery_change_hours'], 500)
        self.assertEqual(result['series'][0]['battery_percent'], 99.95)
        self.assertTrue(all(point['battery_voltage'] is None for point in result['series']))
        self.assertEqual([point['at'] for point in result['series']],
                         sorted(point['at'] for point in result['series']))

    def test_many_simultaneous_cycles_aggregate_without_dividing_by_zero(self):
        for index in range(401):
            self.add(f'cycle-{index}', elapsed_seconds=10, battery_percent=50,
                     metrics={'failures': ['wifi']} if index == 0 else {})
        result = self.data()
        self.assertEqual(result['bucket_seconds'], 1)
        self.assertEqual(len(result['series']), 1)
        self.assertEqual(result['series'][0]['cycles'], 401)
        self.assertEqual(result['series'][0]['failures'], 1)
        self.assertEqual(result['series'][0]['battery_percent'], 50)
        self.assertIsNone(result['summary']['battery_change_pp'])

    def test_analytics_preserves_caller_transaction_and_leaves_no_temp_tables(self):
        self.add('uncommitted', elapsed_seconds=3)
        self.assertTrue(self.db.in_transaction)
        self.data()
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sqlite_temp_master').fetchone()[0], 0)
        self.db.commit()
        self.assertFalse(self.db.in_transaction)
        self.data()
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sqlite_temp_master').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
