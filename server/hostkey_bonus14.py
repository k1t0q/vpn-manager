"""One-time +14-day compensation for active, non-lifetime CAESAR VPN clients.

Never reactivates expired accounts, changes tariff, or touches Xray/Caddy.
An audited campaign ledger makes the operation safe to retry after SSH loss.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3

try:
    from .xray_hostkey import DEFAULT_DB, MOSCOW, check_database
    from .hostkey_bulk_migrate import TEST_MARKERS, parse_ids, private_backup
except ImportError:
    from xray_hostkey import DEFAULT_DB, MOSCOW, check_database
    from hostkey_bulk_migrate import TEST_MARKERS, parse_ids, private_backup


CAMPAIGN = 'caesar_vpn_compensation_2026_11_19_14_days'
DAYS = 14


class CompensationError(Exception):
    pass


def existing_campaign(con: sqlite3.Connection) -> tuple[int, int] | None:
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_billing_campaigns'").fetchone()
    if not exists:
        return None
    return con.execute('SELECT days,affected_clients FROM caesar_billing_campaigns WHERE campaign_id=?',
                       (CAMPAIGN,)).fetchone()


def parse_expiry(value: str) -> date:
    if not isinstance(value, str) or len(value) < 10:
        raise ValueError('Missing date')
    expiry = date.fromisoformat(value[:10])
    if len(value) > 10:
        if value[10] not in ('T', ' '):
            raise ValueError('Invalid separator')
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    return expiry


def calculation(con: sqlite3.Connection, excluded: frozenset[int],
                now: datetime | None = None) -> tuple[dict, list[tuple[int, str, str, str, str]]]:
    now = now or datetime.now(MOSCOW)
    rows = con.execute('SELECT id,user_id,name,period,end_date FROM clients ORDER BY id').fetchall()
    detected_tests = {r[0] for r in rows if any(marker in (r[2] or '').casefold()
                                                for marker in TEST_MARKERS)}
    if detected_tests != excluded:
        raise CompensationError(f'Test exclusions mismatch: expected {sorted(detected_tests)}, got {sorted(excluded)}')
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_client_archives'").fetchone()
    archived = {r[0] for r in con.execute('SELECT client_id FROM caesar_client_archives')} if exists else set()
    unknown, expired, lifetime, updates = [], [], [], []
    for cid, owner, _name, period, end_date in rows:
        if cid in excluded or cid in archived:
            continue
        if period == 'lifetime':
            lifetime.append(cid)
            continue
        try:
            expiry = parse_expiry(end_date)
        except (ValueError, TypeError):
            unknown.append(cid)
            continue
        if expiry < now.date():
            expired.append(cid)
            continue
        # For legacy hourly trial periods, do not grant days if the 24h trial
        # already ended today. Ordinary tariffs expire at Moscow midnight.
        if period == 'trial' and len(end_date) > 10:
            exact = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            if exact.tzinfo is None:
                exact = exact.replace(tzinfo=MOSCOW)
            if exact < now:
                expired.append(cid)
                continue
        new_date = (expiry + timedelta(days=DAYS)).isoformat() + end_date[10:]
        updates.append((cid, owner, period, end_date, new_date))
    applied = existing_campaign(con)
    result = {
        'campaign': CAMPAIGN,
        'days': DAYS,
        'total_clients': len(rows),
        'test_ids_excluded': sorted(excluded),
        'archived_ids_excluded': sorted(archived - excluded),
        'lifetime_ids_excluded': lifetime,
        'expired_ids_excluded': expired,
        'needs_review_ids': unknown,
        'eligible_clients': len(updates),
        'preview': [{'id': cid, 'old_end': old_end, 'new_end': new_end}
                    for cid, _, _, old_end, new_end in updates[:5]],
        'already_applied': applied is not None,
        'previously_compensated': applied[1] if applied else 0,
    }
    return result, updates


def compensate(db: Path, excluded: frozenset[int], backup_dir: Path,
               now: datetime | None = None) -> dict:
    now = now or datetime.now(MOSCOW)
    healthy, _ = check_database(db)
    if not healthy:
        raise CompensationError('SQLite integrity check failed')
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=20) as source:
        first, updates = calculation(source, excluded, now)
    if first['already_applied']:
        return {'ok': True, 'already_applied': True,
                'previously_compensated': first['previously_compensated'], 'changed': 0}
    if first['needs_review_ids']:
        raise CompensationError(f'Unverified end dates: {first["needs_review_ids"]}. Review before applying.')
    if not updates:
        raise CompensationError('No eligible active, non-lifetime clients')
    before_hash = hashlib.sha256(json.dumps(updates, ensure_ascii=False).encode()).hexdigest()
    backup = private_backup(db, backup_dir)
    with sqlite3.connect(db, timeout=30) as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('''CREATE TABLE IF NOT EXISTS caesar_billing_campaigns (
            campaign_id TEXT PRIMARY KEY, days INTEGER NOT NULL, affected_clients INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        if existing_campaign(con):
            return {'ok': True, 'already_applied': True, 'changed': 0, 'backup': str(backup)}
        second, current = calculation(con, excluded, now)
        after_hash = hashlib.sha256(json.dumps(current, ensure_ascii=False).encode()).hexdigest()
        if before_hash != after_hash or second['needs_review_ids']:
            raise CompensationError('Subscriptions changed during backup; no dates modified')
        for cid, owner, period, old_end, new_end in current:
            cursor = con.execute('UPDATE clients SET end_date=? WHERE id=? AND period=? AND end_date=?',
                                 (new_end, cid, period, old_end))
            if cursor.rowcount != 1:
                raise CompensationError(f'Concurrent change for client {cid}; rolled back')
            con.execute('''INSERT INTO client_history(user_id,client_id,event_type,description)
                           VALUES(?,?,?,?)''',
                        (owner, cid, 'compensation_14d',
                         f'{CAMPAIGN}: {old_end} -> {new_end} (+{DAYS} calendar days)'))
        con.execute('''INSERT INTO caesar_billing_campaigns(campaign_id,days,affected_clients)
                       VALUES(?,?,?)''', (CAMPAIGN, DAYS, len(current)))
    return {'ok': True, 'already_applied': False, 'changed': len(updates), 'days_added': DAYS,
            'excluded_expired': len(first['expired_ids_excluded']),
            'excluded_lifetime': len(first['lifetime_ids_excluded']),
            'excluded_tests': sorted(excluded), 'backup': str(backup),
            'xray_caddy_untouched': True}


def main() -> None:
    parser = argparse.ArgumentParser(description='One-time +14 days compensation for active CAESAR VPN subscriptions')
    parser.add_argument('mode', choices=('plan', 'apply'))
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--exclude-test-ids', required=True)
    parser.add_argument('--confirm-compensation', action='store_true')
    parser.add_argument('--backup-dir', type=Path, default=Path('/root/caesar-compensation-backups'))
    args = parser.parse_args()
    try:
        excluded = parse_ids(args.exclude_test_ids)
        if args.mode == 'plan':
            healthy, _ = check_database(args.db)
            if not healthy:
                raise CompensationError('SQLite integrity check failed')
            with sqlite3.connect(f'file:{args.db}?mode=ro', uri=True, timeout=20) as con:
                result, _ = calculation(con, excluded)
            result['dry_run'] = True
        else:
            if os.geteuid() != 0 or not args.confirm_compensation:
                raise CompensationError('Apply requires root and explicit --confirm-compensation')
            result = compensate(args.db, excluded, args.backup_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (CompensationError, sqlite3.Error, OSError, ValueError) as exc:
        parser.exit(2, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
