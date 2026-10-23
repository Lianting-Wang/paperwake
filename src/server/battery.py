"""Conservative battery-life estimates from one measurement per wake cycle.

Only the most recent continuous discharge segment in a seven-day window is used.
This estimates percentage points per elapsed day, never electrical energy.
"""
from datetime import datetime, timedelta, timezone
import uuid

from .history import _cycle_point, _cycles, _mapping, _timestamp, merge_cycle


WINDOW_DAYS = 7
RESERVE_PERCENT = 10
MIN_SAMPLES = 3
MIN_SPAN_HOURS = 6
MIN_DROP_PP = 2
CHARGE_RISE_PP = 2
MAX_GAP_HOURS = 24
MAX_AGE_HOURS = 24


def _battery_point(rows, now, cutoff):
    # Retain history's phase precedence and numeric validation, but do not use a
    # receipt-time fallback as discharge evidence when the device clock is unknown.
    point = _cycle_point(rows, now, None)
    if point is None or point['battery_percent'] is None:
        return None
    payload = merge_cycle(rows)
    measured_at = _timestamp(_mapping(payload.get('metrics')).get('timestamp'))
    started_at = _timestamp(payload.get('started_at'))
    receipts = [_timestamp(row['received_at']) for row in rows]
    receipts = [value for value in receipts if value is not None]
    latest_receipt = max(receipts)
    trusted = (payload.get('time_trusted') is True and started_at is not None
               and started_at <= min(now, min(receipts)))
    point.update(clock_trusted=trusted, received_at=latest_receipt)
    if (trusted and measured_at is not None
            and measured_at <= min(now, latest_receipt)
            and measured_at >= started_at):
        point['at'] = measured_at
    relevant_at = point['at'] if trusted else latest_receipt
    if cutoff is not None and relevant_at < cutoff:
        return None
    return point


def estimate_battery(db, device_id, now):
    """Return a bounded-memory estimate, or an explicit reason to withhold it.

    ``ready`` requires at least three distinct sample times, six elapsed hours
    and a net decrease of two percentage points. An upward move of at least two
    points from the running minimum starts a new possible-charge segment, and a
    gap of more than 24 hours also resets the segment. Smaller fluctuations are
    not summed: the rate uses the segment's net decrease over its actual elapsed
    seconds, avoiding inflated consumption from gauge jitter.

    ``remaining_days`` counts from ``now`` to the estimated 10% reserve crossing,
    allowing for time since the latest reading. Non-ready results expose evidence
    but leave all rate and forecast fields null. The current transaction and all
    persistent device data remain unchanged. A newer unknown-clock battery report
    blocks forecasts based on older trusted samples, including possible backfills.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must include a timezone')
    now = now.astimezone(timezone.utc)
    savepoint = '_battery_' + uuid.uuid4().hex
    db.execute(f'SAVEPOINT {savepoint}')
    try:
        return _estimate(db, device_id, now)
    finally:
        # Creating the temporary table inside this savepoint lets rollback clean
        # up on both success and failure, without committing the caller's writes.
        db.execute(f'ROLLBACK TO {savepoint}')
        db.execute(f'RELEASE {savepoint}')


def _estimate(db, device_id, now):
    result = {'status': 'insufficient', 'daily_consumption_pp': None,
              'remaining_days': None, 'estimated_until': None,
              'reserve_percent': RESERVE_PERCENT, 'window_days': WINDOW_DAYS,
              'sample_count': 0, 'span_hours': 0, 'observed_drop_pp': None,
              'latest_percent': None, 'latest_at': None}
    table = '_battery_points_' + uuid.uuid4().hex
    db.execute(f'CREATE TEMP TABLE {table} (at REAL NOT NULL, percent REAL NOT NULL, received_at REAL NOT NULL)')
    cutoff = now - timedelta(days=WINDOW_DAYS)
    unknown_clock_received = None
    for point, _ in _cycles(db, device_id, now, cutoff, point_builder=_battery_point):
        received = point['received_at'].timestamp()
        if not point['clock_trusted']:
            unknown_clock_received = (received if unknown_clock_received is None
                                      else max(unknown_clock_received, received))
            continue
        db.execute(f'INSERT INTO {table} VALUES (?, ?, ?)',
                   (point['at'].timestamp(), point['battery_percent'], received))

    first = last = minimum = None
    reset_reason = None
    # Distinct cycles with the same timestamp cannot provide additional elapsed
    # evidence. Averaging those readings is deterministic under out-of-order
    # backfills and retains one observation for that instant.
    cursor = db.execute(f'SELECT at, AVG(percent) AS percent, MAX(received_at) FROM {table} GROUP BY at ORDER BY at')
    latest_received = None
    try:
        for row in cursor:
            sample = (row[0], row[1])
            if last is not None and sample[0] - last[0] > MAX_GAP_HOURS * 3600:
                first = None
                reset_reason = 'gap'
            elif minimum is not None and sample[1] - minimum >= CHARGE_RISE_PP:
                first = None
                reset_reason = 'charging'
            if first is None:
                first = sample
                minimum = sample[1]
                result['sample_count'] = 0
            minimum = min(minimum, sample[1])
            last = sample
            latest_received = row[2]
            result['sample_count'] += 1
    finally:
        cursor.close()
    if last is None:
        return result

    span_seconds = last[0] - first[0]
    observed_drop = first[1] - last[1]
    result.update(latest_percent=round(last[1], 4),
                  latest_at=datetime.fromtimestamp(last[0], timezone.utc).isoformat(),
                  span_hours=round(span_seconds / 3600, 4),
                  observed_drop_pp=round(observed_drop, 4))
    if unknown_clock_received is not None and unknown_clock_received >= latest_received:
        # A newly received battery reading with an unknown clock could be a
        # current charge/discharge or an old offline report. Neither can safely
        # be extrapolated from an older trusted sample.
        result['status'] = 'insufficient'
    elif now.timestamp() - last[0] > MAX_AGE_HOURS * 3600:
        result['status'] = 'stale'
    elif result['sample_count'] < MIN_SAMPLES or span_seconds < MIN_SPAN_HOURS * 3600:
        result['status'] = reset_reason or 'insufficient'
    elif observed_drop < MIN_DROP_PP:
        result['status'] = reset_reason or (
            'flat' if minimum >= first[1] - MIN_DROP_PP else 'insufficient')
    else:
        daily = observed_drop * 86400 / span_seconds
        until = datetime.fromtimestamp(last[0], timezone.utc) + timedelta(
            days=max(0, last[1] - RESERVE_PERCENT) / daily)
        result.update(status='ready', daily_consumption_pp=round(daily, 4),
                      remaining_days=round(max(0, (until - now).total_seconds()) / 86400, 4),
                      estimated_until=until.isoformat())
    return result
