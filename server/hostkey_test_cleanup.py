"""Safely retire known CAESAR VPN test clients without orphaning Xray UUIDs.

The two pre-migration unmanaged Xray clients are only inventoried: their
identity cannot be inferred from a UUID alone and must be reviewed separately.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3

try:
    from .hostkey_bulk_migrate import TEST_MARKERS, parse_ids, private_backup
    from .xray_hostkey import (DEFAULT_DB, DEFAULT_XRAY, DEFAULT_BACKUP,
                               MOSCOW, check_database, find_inbound, sync)
except ImportError:
    from hostkey_bulk_migrate import TEST_MARKERS, parse_ids, private_backup
    from xray_hostkey import (DEFAULT_DB, DEFAULT_XRAY, DEFAULT_BACKUP,
                              MOSCOW, check_database, find_inbound, sync)


CAMPAIGN = 'caesar_hostkey_test_cleanup_2026_11_19'
DEPENDENTS = ('caesar_connect_links', 'caesar_vless_devices', 'caesar_panel_test_created',
              'caesar_client_archives', 'client_history')


class CleanupError(Exception):
    pass


def has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?', (name,)).fetchone() is not None


def already_done(con: sqlite3.Connection) -> bool:
    return has_table(con, 'caesar_test_cleanup_ledger') and con.execute(
        'SELECT 1 FROM caesar_test_cleanup_ledger WHERE campaign=?', (CAMPAIGN,)).fetchone() is not None


def inventory(con: sqlite3.Connection, xray: dict, excluded: frozenset[int]) -> tuple[dict, set[str]]:
    rows = con.execute('SELECT id,name FROM clients ORDER BY id').fetchall()
    tests = {cid: name for cid, name in rows if any(marker in (name or '').casefold()
                                                  for marker in TEST_MARKERS)}
    if not already_done(con) and set(tests) != excluded:
        raise CleanupError(f'Expected precisely test clients {sorted(excluded)}; detected {tests}')
    if already_done(con):
        if any(cid in dict(rows) for cid in excluded):
            raise CleanupError('Cleanup ledger exists but test clients are still present')
        return {'already_removed': True, 'test_ids': sorted(excluded)}, set()
    other_refs = con.execute(
        'SELECT id,referrer_id FROM clients WHERE id NOT IN (' + ','.join('?' for _ in excluded) + ') '
        'AND referrer_id IN (' + ','.join('?' for _ in excluded) + ')',
        tuple(excluded) + tuple(excluded)).fetchall()
    if other_refs:
        raise CleanupError(f'Real clients refer to test accounts: {other_refs}')
    # Refuse unknown relational dependencies on clients before physical deletion.
    for (name,) in con.execute('SELECT name FROM sqlite_master WHERE type="table"'):
        safe = name.replace('"', '""')
        for fk in con.execute(f'PRAGMA foreign_key_list("{safe}")'):
            if fk[2] == 'clients' and name not in DEPENDENTS:
                raise CleanupError(f'Unexpected client reference from table {name}')
    uuids = set()
    if has_table(con, 'caesar_vless_devices'):
        uuids = {uuid for (uuid,) in con.execute(
            'SELECT uuid FROM caesar_vless_devices WHERE client_id IN ('
            + ','.join('?' for _ in excluded) + ')', tuple(excluded))}
    all_managed = {r[0] for r in con.execute('SELECT uuid FROM caesar_vless_devices')} if has_table(con, 'caesar_vless_devices') else set()
    current = find_inbound(xray)['settings']['clients']
    unmanaged = [entry for entry in current if entry['id'] not in all_managed]
    return ({'already_removed': False, 'test_ids': sorted(excluded),
             'test_names': {cid: tests[cid] for cid in sorted(excluded)},
             'known_test_device_keys': len(uuids),
             'known_test_keys_still_in_xray': sum(entry['id'] in uuids for entry in current),
             'unmanaged_xray_profiles_not_removed': [
                 {'email': entry.get('email', '(no label)'), 'uuid_suffix': entry['id'][-6:]}
                 for entry in unmanaged]}, uuids)


def apply(db: Path, xray_file: Path, test_ids: frozenset[int],
          db_backups: Path, xray_backups: Path) -> dict:
    healthy, count = check_database(db)
    if not healthy or count < len(test_ids):
        raise CleanupError('Unexpected database state')
    current_xray = json.loads(xray_file.read_text())
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=20) as con:
        before, uuids = inventory(con, current_xray, test_ids)
    if before['already_removed']:
        return {'ok': True, 'already_removed': True, 'changed': 0}
    backup = private_backup(db, db_backups)

    # Keep UUID rows until reconciliation removes them from Xray. Deleting
    # them first would cause the reconciler to preserve them as "unmanaged".
    with sqlite3.connect(db, timeout=30) as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('UPDATE caesar_vless_devices SET manually_enabled=0 WHERE client_id IN ('
                    + ','.join('?' for _ in test_ids) + ')', tuple(test_ids))
    sync_result = sync(db, xray_file, xray_backups, apply=True)
    xray_after = json.loads(xray_file.read_text())
    remaining = {entry['id'] for entry in find_inbound(xray_after)['settings']['clients']}
    if remaining & uuids:
        raise CleanupError('Known test keys remain in Xray; database rows preserved for safe retry')

    with sqlite3.connect(db, timeout=30) as con:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN IMMEDIATE')
        for name in DEPENDENTS:
            if has_table(con, name):
                field = 'id' if name == 'clients' else 'client_id'
                con.execute(f'DELETE FROM {name} WHERE {field} IN ('
                            + ','.join('?' for _ in test_ids) + ')', tuple(test_ids))
        removed = con.execute('DELETE FROM clients WHERE id IN ('
                              + ','.join('?' for _ in test_ids) + ')', tuple(test_ids)).rowcount
        if removed != len(test_ids):
            raise CleanupError('Test client count changed; transaction rolled back')
        con.execute('''CREATE TABLE IF NOT EXISTS caesar_test_cleanup_ledger (
            campaign TEXT PRIMARY KEY, removed_ids TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        con.execute('INSERT INTO caesar_test_cleanup_ledger(campaign,removed_ids) VALUES(?,?)',
                    (CAMPAIGN, ','.join(map(str, sorted(test_ids)))))
    healthy, final_count = check_database(db)
    if not healthy:
        raise CleanupError('Post-cleanup database integrity check failed; restore backup immediately')
    return {'ok': True, 'removed_test_clients': sorted(test_ids), 'remaining_clients': final_count,
            'removed_known_test_keys': len(uuids),
            'unmanaged_xray_profiles_preserved': len(before['unmanaged_xray_profiles_not_removed']),
            'backup': str(backup), 'xray_sync': sync_result}


def main() -> None:
    parser = argparse.ArgumentParser(description='Safely retire known CAESAR VPN test accounts')
    parser.add_argument('mode', choices=('plan', 'apply'))
    parser.add_argument('--test-ids', required=True)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--xray', type=Path, default=DEFAULT_XRAY)
    parser.add_argument('--confirm-delete-tests', action='store_true')
    args = parser.parse_args()
    try:
        ids = parse_ids(args.test_ids)
        if args.mode == 'plan':
            with sqlite3.connect(f'file:{args.db}?mode=ro', uri=True) as con:
                result, _ = inventory(con, json.loads(args.xray.read_text()), ids)
            result['dry_run'] = True
        else:
            if os.geteuid() != 0 or not args.confirm_delete_tests:
                raise CleanupError('Root and --confirm-delete-tests required')
            result = apply(args.db, args.xray, ids,
                           Path('/root/caesar-test-cleanup-backups'), DEFAULT_BACKUP)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (CleanupError, sqlite3.Error, OSError, ValueError) as exc:
        parser.exit(2, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
