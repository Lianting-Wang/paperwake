"""Wake downloads remain complete and deterministic across clock changes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.scheduler.wake_plan import compute_next_run, make_wake_plan, validate_policy, validate_wake_plan


class WakePlanTests(unittest.TestCase):
    def test_full_day_and_minimum_five_with_complete_sorted_policy(self):
        now = datetime(2026, 10, 23, 6, 1, 12, tzinfo=timezone.utc)
        half_hour = [f'{hour:02}:{minute:02}' for hour in range(24) for minute in (0, 30)]
        policy = {'timezone': 'America/New_York', 'schedule': list(reversed(half_hour))}
        plan = make_wake_plan(policy, now, version=7)
        self.assertEqual(plan['version'], 7)
        self.assertEqual(plan['schedule'], half_hour)
        self.assertEqual(len(plan['occurrences']), 48)
        self.assertEqual(plan['occurrences'][0], '2026-10-23T06:30:00+00:00')
        self.assertEqual(plan['occurrences'][-1], '2026-10-24T06:00:00+00:00')
        self.assertEqual(validate_wake_plan(plan), plan)
        daily = make_wake_plan({'timezone': 'UTC', 'schedule': ['06:00']}, now)
        self.assertEqual(len(daily['occurrences']), 5)
        self.assertEqual(daily['occurrences'][-1], '2026-10-28T06:00:00+00:00')

    def test_exact_time_is_excluded_and_24_hour_boundary_is_included(self):
        now = datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc)
        plan = make_wake_plan({'timezone': 'UTC', 'schedule': ['06:00', '12:00']}, now)
        self.assertEqual(plan['occurrences'][:2], ['2026-10-23T12:00:00+00:00', '2026-10-24T06:00:00+00:00'])
        self.assertEqual(len(plan['occurrences']), 5)
        self.assertEqual(compute_next_run(now, ['06:00']), now + timedelta(days=1))

    def test_spring_gap_and_autumn_fold_follow_complete_daily_policy(self):
        spring = make_wake_plan({'timezone': 'America/New_York', 'schedule': ['02:30']},
                                datetime(2026, 3, 7, 8, tzinfo=timezone.utc))
        self.assertEqual(spring['occurrences'][0], '2026-03-09T06:30:00+00:00')
        autumn = make_wake_plan({'timezone': 'America/New_York', 'schedule': ['01:30']},
                                datetime(2026, 11, 1, 5, 31, tzinfo=timezone.utc))
        self.assertEqual(autumn['occurrences'][0], '2026-11-02T06:30:00+00:00')
        local = datetime(2026, 11, 1, 1, 31, tzinfo=ZoneInfo('America/New_York'), fold=0)
        self.assertEqual(compute_next_run(local, ['01:30']).isoformat(), '2026-11-02T01:30:00-05:00')
        self.assertEqual(validate_wake_plan(spring), spring)
        self.assertEqual(validate_wake_plan(autumn), autumn)

    def test_dense_schedule_fits_http_response_budget_and_has_unique_times(self):
        schedule = [f'{hour:02}:{minute:02}' for hour in range(24) for minute in range(60)]
        plan = make_wake_plan({'timezone': 'UTC', 'schedule': schedule},
                              datetime(2026, 10, 23, tzinfo=timezone.utc))
        self.assertEqual(len(plan['occurrences']), 1440)
        self.assertLess(len(json.dumps(dict(debug=False, content_version=1, display={}, wake_plan=plan),
                                       separators=(',', ':')).encode()), 65536)
        self.assertEqual(len(set(plan['occurrences'])), 1440)
        self.assertEqual(validate_wake_plan(plan), plan)

    def test_rejects_invalid_policy_instead_of_silently_changing_it(self):
        policies = [None, {}, {'timezone': 'UTC', 'schedule': []},
                    {'timezone': 'Not/A_Zone', 'schedule': ['08:00']},
                    {'timezone': '../etc/passwd', 'schedule': ['08:00']},
                    {'timezone': 'UTC', 'schedule': ['08:00', '08:00']},
                    {'timezone': 'UTC', 'schedule': ['8:00']},
                    {'timezone': 'UTC', 'schedule': ['24:00']},
                    {'timezone': 'UTC', 'schedule': ['０８:００']},
                    {'timezone': 'UTC', 'schedule': ['08:00'], 'debug': True}]
        for policy in policies:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate_policy(policy)

    def test_untrusted_download_cannot_inject_dates_or_incomplete_policy(self):
        old = make_wake_plan({'timezone': 'America/New_York', 'schedule': ['08:00']},
                             datetime(2020, 1, 1, tzinfo=timezone.utc), version=3)
        self.assertEqual(validate_wake_plan(old), old)  # Old cache keeps its daily policy.
        changes = [{'version': True}, {'version': 0}, {'version': 2147483648},
                   {'generated_at': '2026-01-01T00:00:00'}, {'generated_at': 'bad'},
                   {'generated_at': '2020-01-01T00:00:00+01:00'},
                   {'generated_at': '2020-01-01T00:00:00.001+00:00'},
                   {'generated_at': '9999-12-31T23:59:59+00:00'},
                   {'generated_at': '0001-01-01T00:00:00+00:00'},
                   {'occurrences': old['occurrences'][:-1]},
                   {'occurrences': list(reversed(old['occurrences']))},
                   {'occurrences': [old['occurrences'][0]] * 5},
                   {'occurrences': ['2030-01-01T00:00:00+00:00'] * 5},
                   {'schedule': ['09:00']}, {'extra': 'ignored?'}]
        for change in changes:
            value = deepcopy(old)
            value.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_wake_plan(value)


if __name__ == '__main__':
    unittest.main()
