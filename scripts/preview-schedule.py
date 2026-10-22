#!/usr/bin/env python3
"""Preview schedule calculations without connecting to or changing the Pi."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from project_config import load_config, load_scheduler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--timezone', help='Override the configured timezone for comparison')
    parser.add_argument('--now', help='Optional ISO time for a reproducible preview')
    parser.add_argument('--count', type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.count <= 1000:
        parser.error('count must be between 1 and 1000')
    try:
        cfg = load_config(args.config)
        zone = ZoneInfo(args.timezone or cfg['timezone'])
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(zone)
        now = now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
        scheduler = load_scheduler()
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(f'Config: {args.config}')
    print(f'Timezone: {zone.key}; starting at {now.isoformat()}')
    print(f'{len(cfg["schedule"])} planned refreshes per day: ' + ', '.join(sorted(cfg['schedule'])))
    for _ in range(args.count):
        now = scheduler.compute_next_run(now, cfg['schedule'])
        print(now.isoformat())
    print('Preview only. An actual boot runs its task immediately before scheduling the next wake.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
