"""Daily wake policies and bounded future occurrences, shared by device and server."""
from datetime import datetime, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


HH_MM = re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d', re.ASCII)
MAX_OCCURRENCES = 1500


def validate_policy(value):
    """Validate a complete daily wall-clock schedule and return a canonical copy."""
    if not isinstance(value, dict) or set(value) != {'timezone', 'schedule'}:
        raise ValueError('Wake policy must contain timezone and schedule.')
    name, schedule = value['timezone'], value['schedule']
    if not isinstance(name, str) or not name or len(name) > 128:
        raise ValueError('Wake timezone must be an IANA timezone.')
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError('Wake timezone must be an IANA timezone.') from exc
    if not isinstance(schedule, list) or not 1 <= len(schedule) <= 1440:
        raise ValueError('Wake schedule must contain 1 to 1440 daily times.')
    if any(not isinstance(item, str) or not HH_MM.fullmatch(item) for item in schedule):
        raise ValueError('Wake times must use HH:MM, from 00:00 to 23:59.')
    if len(set(schedule)) != len(schedule):
        raise ValueError('Wake times must not repeat.')
    return {'timezone': name, 'schedule': sorted(schedule)}


def compute_next_run(now, schedule):
    """Skip nonexistent wall times; run a repeated DST wall time only at fold=0."""
    if not schedule:
        raise ValueError('empty schedule')
    candidates = []
    for day in range(3):
        for value in schedule:
            if not isinstance(value, str) or not HH_MM.fullmatch(value):
                raise ValueError(f'invalid HH:MM: {value}')
            hh, mm = map(int, value.split(':'))
            cand = (now + timedelta(days=day)).replace(hour=hh, minute=mm, second=0, microsecond=0, fold=0)
            if now.tzinfo is None:
                if cand > now:
                    candidates.append(cand)
            else:
                restored = datetime.fromtimestamp(cand.timestamp(), now.tzinfo)
                if (restored.replace(tzinfo=None) == cand.replace(tzinfo=None) and restored.fold == 0
                        and cand.timestamp() > now.timestamp()):
                    candidates.append(cand)
    if not candidates:
        raise ValueError('no valid future wake time')
    return min(candidates, key=lambda item: item.timestamp() if item.tzinfo else item)


def _utc_seconds(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Wake generation time must include a UTC offset.')
    return value.astimezone(timezone.utc).replace(microsecond=0)


def make_wake_plan(policy, now, version=1):
    """Include every next-24-hour wake and at least five future occurrences."""
    policy = validate_policy(policy)
    if type(version) is not int or not 1 <= version <= 2147483647:
        raise ValueError('Wake version must be a positive 32-bit integer.')
    generated = _utc_seconds(now)
    horizon = generated + timedelta(hours=24)
    zone = ZoneInfo(policy['timezone'])
    local = generated.astimezone(zone)
    candidates = []
    # Enumerate dates once rather than recomputing the entire daily table for
    # every occurrence. Even an every-minute policy stays inexpensive on a Pi.
    for day in range(8):
        for item in policy['schedule']:
            hour, minute = map(int, item.split(':'))
            wall = (local + timedelta(days=day)).replace(
                hour=hour, minute=minute, second=0, microsecond=0, fold=0)
            candidate = wall.astimezone(timezone.utc)
            restored = candidate.astimezone(zone)
            if (candidate > generated and restored.replace(tzinfo=None) == wall.replace(tzinfo=None)
                    and restored.fold == 0):
                candidates.append(candidate)
        if (len(candidates) >= 5
                and (local + timedelta(days=day)).date() >= horizon.astimezone(zone).date()):
            break
    candidates.sort()
    occurrences = [item.isoformat(timespec='seconds') for index, item in enumerate(candidates)
                   if item <= horizon or index < 5]
    if not 5 <= len(occurrences) <= MAX_OCCURRENCES:
        raise ValueError('Wake plan exceeds its occurrence limit.')
    result = dict(version=version, **policy, generated_at=generated.isoformat(timespec='seconds'),
                  occurrences=occurrences)
    if len(json.dumps(result, separators=(',', ':')).encode('utf-8')) > 65536:
        raise ValueError('Wake plan exceeds its response size limit.')
    return result


def validate_wake_plan(value):
    """Validate cached/downloaded data independently of the device's current clock."""
    if not isinstance(value, dict) or set(value) != {
            'version', 'timezone', 'schedule', 'generated_at', 'occurrences'}:
        raise ValueError('Wake plan has invalid fields.')
    raw = value['generated_at']
    if not isinstance(raw, str) or len(raw) > 40:
        raise ValueError('Wake generation time must be a UTC timestamp.')
    try:
        generated = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if generated.tzinfo is None or generated.utcoffset() != timedelta(0) or generated.microsecond:
            raise ValueError('Wake generation time must be whole UTC seconds.')
    except (ValueError, OverflowError) as exc:
        raise ValueError('Wake generation time must be a UTC timestamp.') from exc
    occurrences = value['occurrences']
    if not isinstance(occurrences, list) or not 5 <= len(occurrences) <= MAX_OCCURRENCES:
        raise ValueError('Wake plan must contain 5 to 1500 occurrences.')
    try:
        expected = make_wake_plan({'timezone': value['timezone'], 'schedule': value['schedule']},
                                  generated, value['version'])
    except (OverflowError, OSError) as exc:
        raise ValueError('Wake generation time is outside the supported calendar range.') from exc
    # The generated list is canonical, increasing, unique and consistent with DST
    # rules and the complete daily policy. Never trust arbitrary absolute dates.
    if occurrences != expected['occurrences']:
        raise ValueError('Wake occurrences do not match the daily policy and generation time.')
    return expected
