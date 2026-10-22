"""Offline configuration checks shared by maintenance tools."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_config(path):
    """Use exactly the same validation and defaults as the deployed scheduler."""
    return load_scheduler().load_config(path)


def load_scheduler():
    """Import definitions only; pisched's guarded main() is never invoked."""
    spec = importlib.util.spec_from_file_location('epaper_pisched', ROOT / 'src/scheduler/pisched.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
