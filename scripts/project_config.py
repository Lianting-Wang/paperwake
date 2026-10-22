"""Offline configuration checks shared by maintenance tools."""
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_config(path):
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('configuration must be a JSON object')
    schedule = raw.get('schedule')
    if not isinstance(schedule, list) or not schedule:
        raise ValueError('schedule must contain at least one HH:MM time')
    if any(not isinstance(t, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t)
           for t in schedule):
        raise ValueError('schedule entries must use 24-hour HH:MM format')
    if len(set(schedule)) != len(schedule):
        raise ValueError('schedule contains duplicate times')
    # The preserved scheduler computes every day; it does not filter weekdays.
    if type(raw.get('repeat_mask')) is not int or raw['repeat_mask'] != 127:
        raise ValueError('this scheduler supports daily schedules only (repeat_mask=127)')
    for section in ('pisugar', 'task', 'shutdown'):
        if not isinstance(raw.get(section), dict):
            raise ValueError(f'missing object: {section}')
    for section in ('pisugar', 'task'):
        timeout = raw[section].get('timeout_sec')
        if type(timeout) is not int or timeout < (1 if section == 'pisugar' else 0):
            raise ValueError(f'{section}.timeout_sec has an invalid value')
    for value in (raw['pisugar'].get('uds_path'), raw.get('log_file')):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('socket and log paths must be absolute paths')
    for section in ('task', 'shutdown'):
        command = raw[section].get('command')
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f'{section}.command must be nonempty')
        argv = shlex.split(command)
        if not argv or not Path(argv[0]).is_absolute():
            raise ValueError(f'{section}.command must start with an absolute executable path')
    if type(raw['shutdown'].get('shutdown_even_if_alarm_set_failed')) is not bool:
        raise ValueError('shutdown_even_if_alarm_set_failed must be a JSON boolean')
    return raw


def load_scheduler():
    """Import definitions only; pisched's guarded main() is never invoked."""
    spec = importlib.util.spec_from_file_location('epaper_pisched', ROOT / 'src/scheduler/pisched.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
