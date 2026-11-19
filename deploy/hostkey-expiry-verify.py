"""Read-only check that paid-through dates match *actual* Xray managed keys."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
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
    # The panel reads the persisted deployed flag, so audit it separately from
    # the real Xray configuration. A stale flag can leave "Отключается…" on
    # screen even when the VPN key has already been removed from Xray.
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as con:
        markers = con.execute('SELECT client_id,kind,uuid,deployed FROM caesar_vless_devices').fetchall()
    stale = [(cid, kind) for cid, kind, uuid, deployed in markers if bool(deployed) != (uuid in users)]
    return {'ok': not (incorrectly_enabled or blocked_manually or expected_missing or stale),
            'managed_devices': len(devices), 'expired_devices': len(expired),
            'expired_keys_still_active': len(incorrectly_enabled),
            'manually_blocked_keys_still_active': len(blocked_manually),
            'paid_keys_not_yet_active': len(expected_missing),
            'stale_deployed_markers': len(stale),
            'stale_status_client_ids': sorted({cid for cid, _ in stale})}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--xray', type=Path, default=DEFAULT_XRAY)
    args = parser.parse_args()
    result = inspect(args.db, args.xray)
    print(json.dumps(result, ensure_ascii=False))
    if not result['ok']:
        raise SystemExit('Xray permissions or panel status markers differ; check expiry timer and reconcile first')


if __name__ == '__main__':
    main()
