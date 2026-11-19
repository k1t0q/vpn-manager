"""Prepare or register two HOSTKEY VLESS profiles per real billing client.

The default operation only prints an inventory. Applying requires explicit
confirmation counts and an explicit list of test clients to exclude. It writes
only to a backed-up SQLite database; the existing Xray timer publishes access.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import uuid

try:
    from .xray_hostkey import DEFAULT_DB, MOSCOW, active_subscription, check_database, init_tables
except ImportError:
    from xray_hostkey import DEFAULT_DB, MOSCOW, active_subscription, check_database, init_tables


KINDS = ('phone', 'pc')
TEST_MARKERS = ('test', 'тест', 'staging', 'pilot')


class BulkMigrationError(Exception):
    pass


def parse_ids(value: str) -> frozenset[int]:
    if not re.fullmatch(r'[1-9][0-9]*(?:,[1-9][0-9]*)*', value):
        raise BulkMigrationError('Provide explicit test IDs, e.g. 88,92,93')
    ids = [int(item) for item in value.split(',')]
    if len(ids) != len(set(ids)):
        raise BulkMigrationError('Duplicate excluded test ID')
    return frozenset(ids)


def plan(con: sqlite3.Connection, excluded: frozenset[int]) -> dict:
    rows = con.execute('SELECT id,user_id,name,period,end_date FROM clients ORDER BY id').fetchall()
    by_id = {row[0]: row for row in rows}
    detected_tests = {row[0] for row in rows if any(
        marker in (row[2] or '').casefold() for marker in TEST_MARKERS)}
    if excluded != detected_tests:
        missing = sorted(detected_tests - excluded)
        unexpected = sorted(excluded - detected_tests)
        raise BulkMigrationError(f'Test exclusions must match labels; missing={missing}, unexpected={unexpected}')
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_client_archives'").fetchone()
    archived = {r[0] for r in con.execute('SELECT client_id FROM caesar_client_archives')} if exists else set()
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_vless_devices'").fetchone()
    devices: dict[int, set[str]] = {}
    if exists:
        for cid, kind in con.execute('SELECT client_id,kind FROM caesar_vless_devices'):
            devices.setdefault(cid, set()).add(kind)
    eligible = [r for r in rows if r[0] not in excluded and r[0] not in archived]
    pending = [r for r in eligible if not set(KINDS).issubset(devices.get(r[0], set()))]
    is_active = lambda r: active_subscription(r[3], r[4])
    return {
        'total_clients': len(rows),
        'test_ids_excluded': sorted(excluded),
        'archived_ids_excluded': sorted(archived - excluded),
        'real_clients': len(eligible),
        'active_real_clients': sum(map(is_active, eligible)),
        'inactive_real_clients': sum(not is_active(r) for r in eligible),
        'already_with_two_keys': len(eligible) - len(pending),
        'clients_to_register': len(pending),
        'active_to_register': sum(map(is_active, pending)),
        'inactive_to_register': sum(not is_active(r) for r in pending),
        'pending_ids': [r[0] for r in pending],
    }


def private_backup(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    filename = 'clients-before-vless-bulk-' + datetime.now(MOSCOW).strftime('%Y%m%d-%H%M%S-%f') + '-' + secrets.token_hex(4) + '.db'
    dest = backup_dir / filename
    handle = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(handle)
    try:
        with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as source:
            with sqlite3.connect(dest) as target:
                source.backup(target)
        os.chmod(dest, 0o600)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return dest


def apply(db_path: Path, excluded: frozenset[int], expected_new: int,
          expected_active: int, backup_dir: Path) -> dict:
    healthy, _ = check_database(db_path)
    if not healthy:
        raise BulkMigrationError('SQLite integrity check failed')
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=20) as con:
        before = plan(con, excluded)
    if before['clients_to_register'] != expected_new or before['active_to_register'] != expected_active:
        raise BulkMigrationError('Confirmation counts changed; run plan again')
    backup = private_backup(db_path, backup_dir)
    with sqlite3.connect(db_path, timeout=30) as con:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN IMMEDIATE')
        init_tables(con)
        current = plan(con, excluded)
        if current['clients_to_register'] != expected_new or current['active_to_register'] != expected_active:
            raise BulkMigrationError('Database changed during staging; nothing committed')
        for cid in current['pending_ids']:
            con.execute('INSERT OR IGNORE INTO caesar_connect_links(client_id,token) VALUES(?,?)',
                        (cid, secrets.token_urlsafe(32)))
            for kind in KINDS:
                con.execute('''INSERT OR IGNORE INTO caesar_vless_devices(client_id,kind,uuid,token)
                               VALUES(?,?,?,?)''',
                            (cid, kind, str(uuid.uuid4()), secrets.token_urlsafe(32)))
            owner = con.execute('SELECT user_id FROM clients WHERE id=?', (cid,)).fetchone()[0]
            con.execute('''INSERT INTO client_history(user_id,client_id,event_type,description)
                           VALUES(?,?,?,?)''',
                        (owner, cid, 'vless_bulk_issued', 'HOSTKEY: two individual VLESS device credentials registered'))
    return {'ok': True, 'registered_clients': expected_new,
            'eligible_active_registered': expected_active,
            'backup': str(backup), 'xray_changed_directly': False,
            'auto_sync': 'Existing five-minute Xray timer will reconcile active subscriptions'}


def main() -> None:
    parser = argparse.ArgumentParser(description='Guarded CAESAR VPN HOSTKEY bulk profile registration')
    parser.add_argument('mode', choices=('plan', 'apply'))
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--exclude-test-ids', required=True, help='Explicit IDs of ALL test-labelled clients')
    parser.add_argument('--expect-new', type=int)
    parser.add_argument('--expect-active', type=int)
    parser.add_argument('--backup-dir', type=Path, default=Path('/root/caesar-vless-bulk-backups'))
    args = parser.parse_args()
    try:
        excluded = parse_ids(args.exclude_test_ids)
        if args.mode == 'plan':
            healthy, _ = check_database(args.db)
            if not healthy:
                raise BulkMigrationError('SQLite integrity check failed')
            with sqlite3.connect(f'file:{args.db}?mode=ro', uri=True, timeout=20) as con:
                result = plan(con, excluded)
            result['dry_run'] = True
        else:
            if os.geteuid() != 0:
                raise BulkMigrationError('Apply requires root on HOSTKEY')
            if (args.expect_new is None or args.expect_active is None or
                    args.expect_new < 1 or not 0 <= args.expect_active <= args.expect_new):
                raise BulkMigrationError('Apply requires confirmed --expect-new and --expect-active counts')
            result = apply(args.db, excluded, args.expect_new, args.expect_active, args.backup_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (BulkMigrationError, sqlite3.Error, OSError) as exc:
        parser.exit(2, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
