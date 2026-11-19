"""Guarded HOSTKEY billing mutations; Xray remains under the existing timer.

Pilot writes are explicitly allowlisted; full writes require a root-owned
service environment setting after migration checks. Every mutation is
idempotent, audited, and backed up before commit.
No user-request thread runs systemctl or edits Xray configuration directly.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import uuid

try:
    from .xray_hostkey import MOSCOW, init_tables
except ImportError:
    from xray_hostkey import MOSCOW, init_tables


PERIOD_MONTHS = {'1m': 1, '6m': 6, '12m': 12}
PERIODS = set(PERIOD_MONTHS) | {'lifetime'}
KINDS = ('phone', 'pc')
REQUEST_ID_RE = re.compile(r'[A-Za-z0-9_-]{16,96}\Z')


class OperationError(Exception):
    def __init__(self, message: str, status=400):
        super().__init__(message)
        self.status = status


def add_months(base: date, count: int) -> date:
    year, month = divmod(base.year * 12 + base.month - 1 + count, 12)
    month += 1
    return date(year, month, min(base.day, monthrange(year, month)[1]))


def deadline(base: date, period: str) -> str | None:
    if period == 'lifetime':
        return None
    if period not in PERIOD_MONTHS:
        raise OperationError('Unsupported tariff')
    return add_months(base, PERIOD_MONTHS[period]).isoformat()


def safe_date(value: str) -> date:
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value)
    except ValueError:
        raise OperationError('Invalid date') from None


class PanelOperations:
    def __init__(self, db_path: Path, pilot_ids: frozenset[int] = frozenset(),
                 allow_all: bool = False, backup_dir: Path | None = None):
        self.db_path = db_path
        self.pilot_ids = pilot_ids
        self.allow_all = allow_all
        self.backup_dir = backup_dir or db_path.parent / 'panel-backups'

    @classmethod
    def from_environment(cls, db_path: Path) -> 'PanelOperations':
        raw = os.environ.get('CAESAR_PANEL_PILOT_IDS', '').strip()
        if raw and not re.fullmatch(r'[1-9][0-9]*(?:,[1-9][0-9]*)*', raw):
            raise RuntimeError('Invalid CAESAR_PANEL_PILOT_IDS')
        mode = os.environ.get('CAESAR_PANEL_MODE', 'pilot').strip()
        if mode not in ('pilot', 'full'):
            raise RuntimeError('Invalid CAESAR_PANEL_MODE')
        # Full mode can only be enabled via root's guarded deployment script,
        # which writes the protected EnvironmentFile after verifying Xray/DB.
        return cls(db_path, frozenset(int(value) for value in raw.split(',') if value),
                   allow_all=(mode == 'full'))

    @property
    def enabled(self) -> bool:
        return self.allow_all or bool(self.pilot_ids)

    def permitted_ids(self) -> set[int]:
        if not self.enabled:
            return set()
        with sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True, timeout=5) as con:
            created = con.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_panel_test_created'").fetchone()
            generated = {row[0] for row in con.execute('SELECT client_id FROM caesar_panel_test_created')} if created else set()
        return set(self.pilot_ids) | generated

    def backup(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.backup_dir, 0o700)
        target = self.backup_dir / (datetime.now(MOSCOW).strftime('%Y%m%d-%H%M%S-%f') + '-' + secrets.token_hex(5) + '.db')
        try:
            with sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True) as source:
                with sqlite3.connect(target) as destination:
                    source.backup(destination)
            os.chmod(target, 0o600)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    @staticmethod
    def tables(con: sqlite3.Connection) -> None:
        con.execute('''CREATE TABLE IF NOT EXISTS caesar_panel_operations (
            request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, result TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        con.execute('''CREATE TABLE IF NOT EXISTS caesar_panel_test_created (
            client_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        con.execute('''CREATE TABLE IF NOT EXISTS caesar_client_archives (
            client_id INTEGER PRIMARY KEY, archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    @staticmethod
    def history(con: sqlite3.Connection, row: sqlite3.Row | dict, event: str, description: str) -> None:
        con.execute('''INSERT INTO client_history(user_id,client_id,event_type,description)
                       VALUES(?,?,?,?)''', (row['user_id'], row['id'], event, description))

    @staticmethod
    def is_archived(con: sqlite3.Connection, client_id: int) -> bool:
        return con.execute('SELECT 1 FROM caesar_client_archives WHERE client_id=?', (client_id,)).fetchone() is not None

    def allowed(self, con: sqlite3.Connection, client_id: int) -> bool:
        return (self.allow_all or client_id in self.pilot_ids or
                con.execute('SELECT 1 FROM caesar_panel_test_created WHERE client_id=?', (client_id,)).fetchone() is not None)

    def execute(self, action: str, client_id: int | None, data: dict, request_id: str) -> dict:
        if not self.enabled:
            raise OperationError('Actions are still in read-only mode', 403)
        if not isinstance(data, dict) or not REQUEST_ID_RE.fullmatch(request_id):
            raise OperationError('Invalid request')
        if action not in {'create', 'edit', 'extend', 'toggle', 'provision', 'archive', 'restore'}:
            raise OperationError('Unknown operation', 404)
        if action == 'create' and client_id is not None:
            raise OperationError('Unexpected client ID')
        if action != 'create' and (type(client_id) is not int or client_id < 1):
            raise OperationError('Invalid client ID')
        digest = sha256(json.dumps([action, client_id, data], sort_keys=True,
                                   ensure_ascii=False).encode()).hexdigest()
        # Cheap read-only authorization/idempotency checks before creating a
        # sensitive full-database backup. Repeat the checks under BEGIN IMMEDIATE.
        with sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True, timeout=5) as preview:
            existing_table = preview.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_panel_operations'").fetchone()
            if existing_table:
                previous = preview.execute('SELECT request_hash,result FROM caesar_panel_operations WHERE request_id=?',
                                           (request_id,)).fetchone()
                if previous:
                    if previous[0] != digest:
                        raise OperationError('Request identifier reused for different operation', 409)
                    return json.loads(previous[1])
            if action != 'create' and not self.allow_all and client_id not in self.pilot_ids:
                created_table = preview.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_panel_test_created'").fetchone()
                if not created_table or not preview.execute('SELECT 1 FROM caesar_panel_test_created WHERE client_id=?',
                                                            (client_id,)).fetchone():
                    raise OperationError('This client is not enabled for pilot operations', 403)
        self.backup()
        with sqlite3.connect(self.db_path, timeout=15) as con:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('BEGIN IMMEDIATE')
            self.tables(con)
            existing = con.execute('SELECT request_hash,result FROM caesar_panel_operations WHERE request_id=?',
                                   (request_id,)).fetchone()
            if existing:
                if existing['request_hash'] != digest:
                    raise OperationError('Request identifier reused for different operation', 409)
                return json.loads(existing['result'])
            if action == 'create':
                response = self._create(con, data)
            else:
                row = con.execute('SELECT * FROM clients WHERE id=?', (client_id,)).fetchone()
                if row is None:
                    raise OperationError('Client not found', 404)
                if not self.allowed(con, client_id):
                    raise OperationError('This client is not enabled for pilot operations', 403)
                archived = self.is_archived(con, client_id)
                if archived != (action == 'restore') and (archived or action == 'restore'):
                    raise OperationError('Client archive state conflicts with operation', 409)
                response = self._change(con, row, action, data)
            con.execute('''INSERT INTO caesar_panel_operations(request_id,request_hash,result)
                           VALUES(?,?,?)''', (request_id, digest, json.dumps(response, ensure_ascii=False)))
            return response

    def _create(self, con: sqlite3.Connection, data: dict) -> dict:
        if set(data) - {'name', 'phone', 'start_date', 'period', 'referrer_id'}:
            raise OperationError('Unexpected fields')
        name = data.get('name', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 150:
            raise OperationError('Invalid name')
        name = name.strip()
        if not self.allow_all and not re.match(r'^(?:TEST|ТЕСТ)\b', name, flags=re.IGNORECASE):
            raise OperationError('During pilot, new clients must be named TEST ...', 403)
        period = data.get('period')
        if not isinstance(period, str) or period not in PERIODS:
            raise OperationError('Choose 1, 6, 12 months or lifetime')
        today = datetime.now(MOSCOW).date()
        start = safe_date(data.get('start_date') or today.isoformat())
        end = deadline(start, period)
        phone = data.get('phone', '')
        if not isinstance(phone, str) or len(phone) > 100:
            raise OperationError('Invalid phone')
        ref = data.get('referrer_id') or None
        if ref is not None and (type(ref) is not int or not con.execute('SELECT id FROM clients WHERE id=?', (ref,)).fetchone()):
            raise OperationError('Invalid referrer')
        owner = con.execute('SELECT user_id FROM clients ORDER BY id LIMIT 1').fetchone()
        if owner is None:
            raise OperationError('Missing existing billing owner', 409)
        cur = con.execute('''INSERT INTO clients(user_id,name,phone,start_date,end_date,period,note,referrer_id)
                             VALUES(?,?,?,?,?,?,?,?)''',
                          (owner['user_id'], name, phone, start.isoformat(), end, period, '', ref))
        new_id = cur.lastrowid
        if not self.allow_all:
            con.execute('INSERT INTO caesar_panel_test_created(client_id) VALUES(?)', (new_id,))
        row = con.execute('SELECT * FROM clients WHERE id=?', (new_id,)).fetchone()
        self._register(con, row)
        self.history(con, row, 'created', 'Created in HOSTKEY VPN MANAGER; two VLESS devices registered')
        return {'ok': True, 'id': new_id, 'end_date': end, 'sync_pending': True}

    def _change(self, con: sqlite3.Connection, row: sqlite3.Row, action: str, data: dict) -> dict:
        client_id = row['id']
        if action == 'edit':
            if not data or set(data) - {'name','phone','start_date','end_date','period','referrer_id'}:
                raise OperationError('Invalid edit fields')
            changed = dict(data)
            merged = dict(row)
            merged.update(changed)
            name, phone, period = merged['name'], merged['phone'], merged['period']
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 150:
                raise OperationError('Invalid client name')
            if phone is not None and (not isinstance(phone, str) or len(phone) > 100):
                raise OperationError('Invalid phone')
            if not isinstance(period, str) or period not in PERIODS | {'trial','14d','imported','1','6','12'}:
                raise OperationError('Invalid tariff')
            safe_date(merged['start_date'])
            if period == 'lifetime':
                changed['end_date'] = None
            elif not isinstance(merged['end_date'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T[^ ]+)?', merged['end_date']):
                raise OperationError('Invalid subscription end date')
            else:
                safe_date(merged['end_date'][:10])
            ref = merged.get('referrer_id')
            if ref is not None and (type(ref) is not int or ref == client_id or
                                    not con.execute('SELECT id FROM clients WHERE id=?', (ref,)).fetchone()):
                raise OperationError('Invalid referrer')
            columns = ','.join(f'{column}=?' for column in changed)
            con.execute(f'UPDATE clients SET {columns} WHERE id=?', (*changed.values(), client_id))
            self.history(con, row, 'edited', 'Billing details edited in HOSTKEY panel')
        elif action == 'extend':
            if set(data) != {'period'} or not isinstance(data['period'], str) or data['period'] not in PERIODS:
                raise OperationError('Choose 1, 6, 12 months or lifetime')
            period = data['period']
            if row['period'] == 'lifetime' and period != 'lifetime':
                raise OperationError('Lifetime subscription cannot be shortened', 409)
            base = datetime.now(MOSCOW).date()
            if row['end_date'] and row['period'] != 'lifetime':
                try:
                    base = max(base, safe_date(row['end_date'][:10]))
                except OperationError:
                    raise OperationError('Review invalid existing deadline first', 409) from None
            end = deadline(base, period)
            con.execute('UPDATE clients SET period=?,end_date=? WHERE id=?', (period, end, client_id))
            self.history(con, row, 'extended', f'HOSTKEY subscription extended: {period} to {end or "lifetime"}')
        elif action == 'toggle':
            if set(data) != {'kind', 'enabled'} or data['kind'] not in KINDS or type(data['enabled']) is not bool:
                raise OperationError('Choose phone/pc and true/false')
            cursor = con.execute('''UPDATE caesar_vless_devices SET manually_enabled=?
                                    WHERE client_id=? AND kind=?''',
                                 (int(data['enabled']), client_id, data['kind']))
            if cursor.rowcount != 1:
                raise OperationError('Issue two keys before changing device access', 409)
            self.history(con, row, 'device_access',
                         f'HOSTKEY {data["kind"]} {"enabled" if data["enabled"] else "disabled"}')
        elif action == 'provision':
            if data:
                raise OperationError('Provision takes no fields')
            self._register(con, row)
            self.history(con, row, 'vless_issued', 'Two HOSTKEY VLESS device profiles ensured')
        elif action == 'archive':
            if set(data) != {'confirm_name'} or data['confirm_name'] != row['name']:
                raise OperationError('Client name confirmation does not match', 409)
            con.execute('INSERT INTO caesar_client_archives(client_id) VALUES(?)', (client_id,))
            # Retain UUIDs so the Xray reconciler can remove them from the live
            # inbound instead of mistakenly treating them as unmanaged users.
            con.execute('''UPDATE caesar_vless_devices SET manually_enabled=0,token=lower(hex(randomblob(24)))
                           WHERE client_id=?''', (client_id,))
            con.execute('''UPDATE caesar_connect_links SET token=lower(hex(randomblob(24)))
                           WHERE client_id=?''', (client_id,))
            self.history(con, row, 'archived', 'HOSTKEY account archived; credentials revoked')
        elif action == 'restore':
            if data:
                raise OperationError('Restore takes no fields')
            con.execute('DELETE FROM caesar_client_archives WHERE client_id=?', (client_id,))
            self.history(con, row, 'restored', 'HOSTKEY account restored; devices remain disabled pending review')
        return {'ok': True, 'id': client_id, 'sync_pending': action in {'edit','extend','toggle','provision','archive'}}

    @staticmethod
    def _register(con: sqlite3.Connection, row: sqlite3.Row) -> None:
        init_tables(con)
        con.execute('INSERT OR IGNORE INTO caesar_connect_links(client_id,token) VALUES(?,?)',
                    (row['id'], secrets.token_urlsafe(32)))
        for kind in KINDS:
            con.execute('''INSERT OR IGNORE INTO caesar_vless_devices(client_id,kind,uuid,token)
                           VALUES(?,?,?,?)''',
                        (row['id'], kind, str(uuid.uuid4()), secrets.token_urlsafe(32)))
