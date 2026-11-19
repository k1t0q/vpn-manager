"""One-time additional +9 days for clients who received HOSTKEY +14 days.

Uses the original compensation history as the cohort. New, expired, lifetime,
archived and test accounts are not extended. No Xray/Caddy changes are made.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3

try:
    from .xray_hostkey import DEFAULT_DB, MOSCOW, check_database
    from .hostkey_bulk_migrate import TEST_MARKERS, parse_ids, private_backup
    from .hostkey_bonus14 import CAMPAIGN as BONUS14_CAMPAIGN, CompensationError, parse_expiry
except ImportError:
    from xray_hostkey import DEFAULT_DB, MOSCOW, check_database
    from hostkey_bulk_migrate import TEST_MARKERS, parse_ids, private_backup
    from hostkey_bonus14 import CAMPAIGN as BONUS14_CAMPAIGN, CompensationError, parse_expiry


CAMPAIGN = 'caesar_vpn_compensation_2026_11_19_additional_9_days'
DAYS = 9


def calculation(con: sqlite3.Connection, excluded: frozenset[int],
                now: datetime | None = None) -> tuple[dict, list[tuple[int, str, str, str, str]]]:
    now = now or datetime.now(MOSCOW)
    campaigns = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='caesar_billing_campaigns'").fetchone()
    if not campaigns:
        raise CompensationError('Original +14-day campaign is missing; refusing +9 days')
    original = con.execute('SELECT days,affected_clients FROM caesar_billing_campaigns WHERE campaign_id=?',
                           (BONUS14_CAMPAIGN,)).fetchone()
    if not original or original[0] != 14:
        raise CompensationError('Original +14-day campaign was not verified')
    old_events = con.execute('''SELECT client_id FROM client_history
        WHERE event_type='compensation_14d' AND description LIKE ?''', (BONUS14_CAMPAIGN + ':%',)).fetchall()
    previous_ids = {row[0] for row in old_events}
    if len(old_events) != original[1] or len(previous_ids) != original[1]:
        raise CompensationError('Original +14-day history does not match campaign ledger')
    previous = con.execute('SELECT days,affected_clients FROM caesar_billing_campaigns WHERE campaign_id=?',
                           (CAMPAIGN,)).fetchone()
    if previous and previous[0] != DAYS:
        raise CompensationError('Existing +9-day campaign has unexpected value')
    rows = con.execute('SELECT id,user_id,name,period,end_date FROM clients ORDER BY id').fetchall()
    present_ids = {row[0] for row in rows}
    detected_tests = {row[0] for row in rows if any(marker in (row[2] or '').casefold()
                                                     for marker in TEST_MARKERS)}
    # Previously excluded test IDs may have been deleted at the end of migration.
    if detected_tests != (excluded & present_ids):
        raise CompensationError(f'Test exclusions mismatch: expected {sorted(detected_tests)}, got {sorted(excluded)}')
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_client_archives'").fetchone()
    archived = {row[0] for row in con.execute('SELECT client_id FROM caesar_client_archives')} if exists else set()
    updates, expired, lifetime, unknown = [], [], [], []
    for cid, owner, _name, period, end_date in rows:
        if cid not in previous_ids or cid in excluded or cid in archived:
            continue
        if period == 'lifetime':
            lifetime.append(cid)
            continue
        try:
            date_end = parse_expiry(end_date)
        except (TypeError, ValueError):
            unknown.append(cid)
            continue
        if date_end < now.date():
            expired.append(cid)
            continue
        if period == 'trial' and len(end_date) > 10:
            precise = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            if precise.tzinfo is None:
                precise = precise.replace(tzinfo=MOSCOW)
            if precise < now:
                expired.append(cid)
                continue
        updated = (date_end + timedelta(days=DAYS)).isoformat() + end_date[10:]
        updates.append((cid, owner, period, end_date, updated))
    plan = {'campaign': CAMPAIGN, 'days': DAYS, 'prior_bonus_clients': original[1],
            'eligible_clients': len(updates), 'excluded_tests': sorted(excluded),
            'excluded_archived': sorted(archived & previous_ids),
            'excluded_expired': expired, 'excluded_lifetime': lifetime, 'needs_review': unknown,
            'already_applied': previous is not None,
            'previously_compensated': previous[1] if previous else 0,
            'preview': [] if previous else [{'id': row[0], 'old_end': row[3], 'new_end': row[4]} for row in updates[:6]]}
    return plan, updates


def compensate(db: Path, excluded: frozenset[int], backup_dir: Path,
               now: datetime | None = None) -> dict:
    now = now or datetime.now(MOSCOW)
    if not check_database(db)[0]:
        raise CompensationError('SQLite integrity check failed')
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=20) as source:
        first, updates = calculation(source, excluded, now)
    if first['already_applied']:
        return {'ok': True, 'already_applied': True, 'changed': 0}
    if first['needs_review']:
        raise CompensationError(f'Unverified dates: {first["needs_review"]}')
    if not updates:
        raise CompensationError('No eligible recipients from original +14-day campaign')
    before = hashlib.sha256(json.dumps(updates, ensure_ascii=False).encode()).hexdigest()
    backup = private_backup(db, backup_dir)
    with sqlite3.connect(db, timeout=30) as con:
        con.execute('BEGIN IMMEDIATE')
        second, current = calculation(con, excluded, now)
        after = hashlib.sha256(json.dumps(current, ensure_ascii=False).encode()).hexdigest()
        if before != after or second['already_applied'] or second['needs_review']:
            raise CompensationError('Subscriptions changed during backup; no updates applied')
        for cid, owner, period, old_end, new_end in current:
            cursor = con.execute('UPDATE clients SET end_date=? WHERE id=? AND period=? AND end_date=?',
                                 (new_end, cid, period, old_end))
            if cursor.rowcount != 1:
                raise CompensationError(f'Concurrent change for client #{cid}; rolled back')
            con.execute('''INSERT INTO client_history(user_id,client_id,event_type,description)
                           VALUES(?,?,?,?)''', (owner, cid, 'compensation_9d',
                                              f'{CAMPAIGN}: {old_end} -> {new_end} (+{DAYS} calendar days)'))
        con.execute('INSERT INTO caesar_billing_campaigns(campaign_id,days,affected_clients) VALUES(?,?,?)',
                    (CAMPAIGN, DAYS, len(current)))
    return {'ok': True, 'already_applied': False, 'changed': len(current), 'days_added': DAYS,
            'previous_campaign_recipients': first['prior_bonus_clients'],
            'backup': str(backup), 'xray_caddy_untouched': True}


def main() -> None:
    parser = argparse.ArgumentParser(description='Additional +9 days to original HOSTKEY +14-day recipients')
    parser.add_argument('mode', choices=('plan', 'apply'))
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--exclude-test-ids', required=True)
    parser.add_argument('--confirm-compensation', action='store_true')
    parser.add_argument('--backup-dir', type=Path, default=Path('/root/caesar-compensation-backups'))
    args = parser.parse_args()
    try:
        excluded = parse_ids(args.exclude_test_ids)
        if args.mode == 'plan':
            if not check_database(args.db)[0]:
                raise CompensationError('SQLite integrity check failed')
            with sqlite3.connect(f'file:{args.db}?mode=ro', uri=True, timeout=20) as con:
                result, _ = calculation(con, excluded)
            result['dry_run'] = True
        else:
            if os.geteuid() != 0 or not args.confirm_compensation:
                raise CompensationError('Apply requires root and --confirm-compensation')
            result = compensate(args.db, excluded, args.backup_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (CompensationError, sqlite3.Error, OSError, ValueError) as error:
        parser.exit(2, f'ERROR: {error}\n')


if __name__ == '__main__':
    main()
