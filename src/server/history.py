"""Bounded-memory history analytics over immutable device report phases."""
from datetime import datetime, timedelta, timezone
import json
import math
import uuid


MAX_POINTS = 400
PHASE_ORDER = {'check-in': 0, 'final': 1, 'outcome': 2}
METRICS = {
    'battery_percent': (0, 100),
    'battery_voltage': (0, 10),
    'temperature_c': (-40, 150),
    'wifi_rssi': (-150, 0),
    'http_latency_ms': (0, 60000),
    'elapsed_seconds': (0, 86400),
}


def _payload(row):
    value = row['payload']
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _merge(previous, current):
    result = dict(previous)
    for key, value in current.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_cycle(rows):
    """Merge one cycle's phase payloads, retaining fields omitted by later phases.

    Lists (including failures) are snapshots and are replaced, never concatenated.
    Receipt order cannot make a late final report overwrite an outcome report.
    """
    result = {}
    for row in sorted(rows, key=lambda row: PHASE_ORDER.get(row['phase'], -1)):
        result = _merge(result, _payload(row))
    return result


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _number(value, low, high):
    if type(value) not in (int, float):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and low <= value <= high else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def cycle_has_failures(payload):
    """Use the same fault definition in cycle records and aggregate charts."""
    explicit_failure = any(_mapping(payload.get(key)).get('status') == 'failed'
                           for key in ('server', 'display', 'task', 'shutdown'))
    return bool(payload.get('failures') or _mapping(payload.get('metrics')).get('failures') or explicit_failure)


def _cycles(db, device_id, now, cutoff, point_builder=None):
    # The primary key supplies event order. At most three report payloads are kept
    # in Python at once, even when retention is permanent.
    if cutoff is None:
        cursor = db.execute('SELECT event_id, phase, received_at, payload FROM events '
                            'WHERE device_id=? ORDER BY event_id, phase', (device_id,))
    else:
        # A sample cannot be later than its last receipt. Wholly old events can
        # therefore be skipped using the device/receipt-time index, including
        # when a custom point builder prefers the metric's actual sample time.
        # Fetch every phase for candidates, including an older check-in whose
        # trusted clock and battery measurement a new backfill may omit.
        cursor = db.execute('WITH candidates AS (SELECT DISTINCT event_id FROM events '
                            'WHERE device_id=? AND received_at>=?) '
                            'SELECT e.event_id, e.phase, e.received_at, e.payload FROM candidates c '
                            'JOIN events e ON e.event_id=c.event_id AND e.device_id=? '
                            'ORDER BY e.event_id, e.phase', (device_id, cutoff.isoformat(), device_id))
    point_builder = point_builder or _cycle_point
    current_id = None
    rows = []
    try:
        for row in cursor:
            if rows and row['event_id'] != current_id:
                point = point_builder(rows, now, cutoff)
                if point is not None:
                    yield point, len(rows)
                rows = []
            current_id = row['event_id']
            rows.append(row)
        if rows:
            point = point_builder(rows, now, cutoff)
            if point is not None:
                yield point, len(rows)
    finally:
        cursor.close()


def _cycle_point(rows, now, cutoff):
    payload = merge_cycle(rows)
    received = [value for row in rows if (value := _timestamp(row['received_at'])) is not None]
    if not received:
        return None
    received_at = min(received)
    started_at = _timestamp(payload.get('started_at')) if payload.get('time_trusted') is True else None
    # Device wall time is useful for outbox backfills, but cannot pull the chart
    # into the future. Without a usable device clock, use the first server receipt.
    at = started_at if started_at is not None and started_at <= min(now, received_at) else received_at
    if at > now or (cutoff is not None and at < cutoff):
        return None
    metrics = _mapping(payload.get('metrics'))
    values = _mapping(metrics.get('values'))
    inputs = dict(values, temperature_c=values.get('temperature'), elapsed_seconds=payload.get('elapsed_seconds'))
    if _number(inputs.get('battery_percent'), *METRICS['battery_percent']) is None:
        inputs['battery_percent'] = payload.get('battery_percent')
    if _number(inputs.get('http_latency_ms'), *METRICS['http_latency_ms']) is None:
        inputs['http_latency_ms'] = _mapping(payload.get('server')).get('latency_ms')
    point = {'at': at}
    point.update({key: _number(inputs.get(key), *bounds) for key, bounds in METRICS.items()})
    point.update(cycles=1, failures=int(cycle_has_failures(payload)))
    return point


def _serialize(point):
    result = dict(point, at=point['at'].isoformat())
    for key in METRICS:
        if result[key] is not None:
            result[key] = round(result[key], 4)
    return result


def build_history_data(db, device_id, now, days):
    """Return cycle analytics and no more than 400 chronological chart samples.

    ``days=None`` means all retained records. Larger series use equal time buckets;
    numeric measurements are averaged only over available values. ``cycles`` and
    ``failures`` are summed, with failures counted once per cycle. Temporary SQLite
    storage sorts duration scalars for an exact median without an unbounded Python
    list. Neither device snapshots nor persistent history rows are changed.
    """
    savepoint = '_history_' + uuid.uuid4().hex
    db.execute(f'SAVEPOINT {savepoint}')
    try:
        return _build_history_data(db, device_id, now, days)
    finally:
        # Keep both scans on the same snapshot, including when no duration was
        # reported. Restore the caller's transaction state and temporary storage.
        db.execute(f'ROLLBACK TO {savepoint}')
        db.execute(f'RELEASE {savepoint}')


def _build_history_data(db, device_id, now, days):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must include a timezone')
    now = now.astimezone(timezone.utc)
    if days == 'all':
        days = None
    if days is not None and (type(days) is not int or days not in (3, 7, 15, 30, 90)):
        raise ValueError('unsupported history range')
    cutoff = now - timedelta(days=days) if days is not None else None
    summary = {'cycles': 0, 'reports': 0, 'failure_cycles': 0, 'latest_battery_percent': None,
               'battery_change_pp': None, 'battery_change_hours': None,
               'median_cycle_seconds': None, 'latest_voltage': None}
    start = end = None
    first_battery = last_battery = last_voltage = None
    duration_count = 0
    raw_points = []
    # This private table belongs to this call and connection. SQLite manages its
    # sort storage; only compact duration numbers, never report JSON, are inserted.
    duration_table = '_history_durations_' + uuid.uuid4().hex
    db.execute(f'CREATE TEMP TABLE {duration_table} (value REAL NOT NULL)')
    try:
        for point, reports in _cycles(db, device_id, now, cutoff):
            at = point['at']
            start = at if start is None else min(start, at)
            end = at if end is None else max(end, at)
            summary['cycles'] += 1
            summary['reports'] += reports
            summary['failure_cycles'] += point['failures']
            battery = point['battery_percent']
            if battery is not None:
                if first_battery is None or at < first_battery[0]:
                    first_battery = (at, battery)
                if last_battery is None or at >= last_battery[0]:
                    last_battery = (at, battery)
            voltage = point['battery_voltage']
            if voltage is not None and (last_voltage is None or at >= last_voltage[0]):
                last_voltage = (at, voltage)
            duration = point['elapsed_seconds']
            if duration is not None:
                db.execute(f'INSERT INTO {duration_table} VALUES (?)', (duration,))
                duration_count += 1
            if summary['cycles'] <= MAX_POINTS:
                raw_points.append(point)
            elif raw_points:
                raw_points.clear()
        if duration_count:
            middle = db.execute(f'SELECT value FROM {duration_table} ORDER BY value LIMIT ? OFFSET ?',
                                (2 - duration_count % 2, (duration_count - 1) // 2)).fetchall()
            summary['median_cycle_seconds'] = round(sum(row[0] for row in middle) / len(middle), 4)
    finally:
        db.execute(f'DROP TABLE IF EXISTS {duration_table}')
    if last_battery is not None:
        summary['latest_battery_percent'] = last_battery[1]
    if first_battery is not None and last_battery[0] > first_battery[0]:
        summary['battery_change_pp'] = round(last_battery[1] - first_battery[1], 4)
        summary['battery_change_hours'] = round((last_battery[0] - first_battery[0]).total_seconds() / 3600, 4)
    if last_voltage is not None:
        summary['latest_voltage'] = last_voltage[1]
    bucket_seconds = 0
    if summary['cycles'] > MAX_POINTS:
        bucket_seconds = max(1, math.ceil((end - start).total_seconds() / (MAX_POINTS - 1)))
        buckets = {}
        for point, _ in _cycles(db, device_id, now, cutoff):
            index = int((point['at'] - start).total_seconds() // bucket_seconds)
            bucket = buckets.setdefault(index, {'seconds': 0, 'cycles': 0, 'failures': 0,
                                                'sums': dict.fromkeys(METRICS, 0),
                                                'counts': dict.fromkeys(METRICS, 0)})
            bucket['seconds'] += (point['at'] - start).total_seconds()
            bucket['cycles'] += 1
            bucket['failures'] += point['failures']
            for key in METRICS:
                if point[key] is not None:
                    bucket['sums'][key] += point[key]
                    bucket['counts'][key] += 1
        raw_points = []
        for index in sorted(buckets):
            bucket = buckets[index]
            point = {'at': start + timedelta(seconds=bucket['seconds'] / bucket['cycles']),
                     'cycles': bucket['cycles'], 'failures': bucket['failures']}
            point.update({key: bucket['sums'][key] / bucket['counts'][key] if bucket['counts'][key] else None
                          for key in METRICS})
            raw_points.append(point)
    raw_points.sort(key=lambda point: point['at'])
    return {'series': [_serialize(point) for point in raw_points], 'summary': summary,
            'coverage': {'start': start.isoformat() if start else None, 'end': end.isoformat() if end else None},
            'bucket_seconds': bucket_seconds}
