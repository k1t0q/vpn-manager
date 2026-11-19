"""Read-only check that paid-through dates match *actual* Xray managed keys."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from xray_hostkey import active_subscription, find_inbound, records, DEFAULT_DB, DEFAULT_XRAY


def inspect(db: Path, xray: Path) -> dict:
    users = {row['id'] for row in find_inbound(json.loads(xray.read_text()))['settings']['clients']}
    devices = records(db)
    expired = [row for row in devices if not active_subscription(row['period'], row['end_date'])]
    incorrectly_enabled = [row for row in expired if row['uuid'] in users]
    blocked_manually = [row for row in devices if not row['manually_enabled'] and row['uuid'] in users]
    expected_missing = [row for row in devices if row['manually_enabled'] and
                        active_subscription(row['period'], row['end_date']) and row['uuid'] not in users]
    return {'ok': not (incorrectly_enabled or blocked_manually or expected_missing),
            'managed_devices': len(devices), 'expired_devices': len(expired),
            'expired_keys_still_active': len(incorrectly_enabled),
            'manually_blocked_keys_still_active': len(blocked_manually),
            'paid_keys_not_yet_active': len(expected_missing)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--xray', type=Path, default=DEFAULT_XRAY)
    args = parser.parse_args()
    result = inspect(args.db, args.xray)
    print(json.dumps(result, ensure_ascii=False))
    if not result['ok']:
        raise SystemExit('Xray permissions differ from billing; check expiry timer and reconcile first')


if __name__ == '__main__':
    main()
