"""Offline checks for schedule boundaries and release/configuration mistakes."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from project_config import load_config, load_scheduler

spec = importlib.util.spec_from_file_location('prepare_release', ROOT / 'scripts/prepare-release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ScheduleTests(unittest.TestCase):
    def test_daytime_schedule_skips_overnight(self):
        scheduler = load_scheduler()
        cfg = load_config(ROOT / 'config/daytime.example.json')
        self.assertEqual(scheduler.compute_next_run(datetime(2026, 10, 22, 22), cfg['schedule']),
                         datetime(2026, 10, 23, 8))

    def test_exact_boundary_selects_a_future_alarm(self):
        scheduler = load_scheduler()
        self.assertEqual(scheduler.compute_next_run(datetime(2026, 10, 22, 12), ['08:00', '12:00', '16:00']),
                         datetime(2026, 10, 22, 16))

    def test_half_hour_rolls_over_midnight(self):
        scheduler = load_scheduler()
        cfg = load_config(ROOT / 'config/test-half-hour.json')
        self.assertEqual(scheduler.compute_next_run(datetime(2026, 10, 22, 23, 59), cfg['schedule']),
                         datetime(2026, 10, 23))


class ConfigurationTests(unittest.TestCase):
    def test_rejects_operationally_invalid_configurations(self):
        original = json.loads((ROOT / 'config/daytime.example.json').read_text())
        for key, value in [('schedule', []), ('schedule', ['24:00']),
                           ('schedule', ['08:00', '08:00']), ('repeat_mask', 1)]:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as folder:
                changed = dict(original, **{key: value})
                path = Path(folder) / 'config.json'
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    load_config(path)


class ReleaseTests(unittest.TestCase):
    def test_wrong_architecture_is_rejected_without_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            binary = folder / 'epd'
            binary.write_bytes(b'\x7fELF\x02\x01' + b'\0' * 12 + (62).to_bytes(2, 'little'))
            with self.assertRaises(ValueError):
                release.prepare(ROOT / 'config/daytime.example.json', binary, folder / 'release')
            self.assertFalse((folder / 'release').exists())

    def test_bundle_keeps_the_explicit_schedule_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            binary = folder / 'epd'
            # Header fixture exercises packaging only; never executed as a program.
            binary.write_bytes(b'\x7fELF\x02\x01' + b'\0' * 12 + (183).to_bytes(2, 'little'))
            config = ROOT / 'config/daytime.example.json'
            output = release.prepare(config, binary, folder / 'release')
            staged = output / 'rootfs/etc/pisched/config.json'
            self.assertEqual(staged.read_bytes(), config.read_bytes())
            self.assertTrue((output / 'rootfs/home/wlt/IT8951/epd').stat().st_mode & 0o111)
            self.assertFalse((output / 'rootfs/boot').exists())
            self.assertEqual((output/'rootfs/opt/pisched/server_client.py').read_bytes(),
                             (ROOT/'src/scheduler/server_client.py').read_bytes())
            self.assertFalse((output/'rootfs/etc/pisched/device.token').exists())
            self.assertEqual((output/'rootfs/home/wlt/IT8951/singlepage/page.c').read_bytes(),
                             (ROOT/'src/display/IT8951/singlepage/page.c').read_bytes())
            with self.assertRaises(ValueError):
                release.prepare(config, binary, output)


if __name__ == '__main__':
    unittest.main()
