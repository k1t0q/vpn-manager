"""Read-only per-key sharing-risk summaries from minimal Xray access activity.

No visited destinations, raw IP addresses, UUIDs, or link tokens are persisted.
The collector runs separately as root and only stores salted IP fingerprints in
a private SQLite database. Risk levels are heuristics, never proof or a ban.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import hmac
import ipaddress
import json
import os
import argparse
import grp
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import time


RISK_DB = Path('/var/lib/caesar-risk-monitor/observations.db')
SALT_FILE = Path('/var/lib/caesar-risk-monitor/risk-salt.key')
XRAY_CONFIG = Path('/usr/local/etc/xray/config.json')
CLIENT_DB = Path('/var/lib/caesar-manager/clients.db')
WINDOW_SECONDS = 300
RETENTION_SECONDS = 7 * 86400
ACCESS_USER = re.compile(r'\bemail:\s*caesar-([1-9][0-9]*)-(phone|pc)\b')


class RiskError(Exception):
    pass


def parse_access(message: str) -> tuple[int, str, str] | None:
    """Extract only the managed user label and public source IP, no destination."""
    match = ACCESS_USER.search(message)
    if not match or ' accepted ' not in message:
        return None
    source = message.split(' accepted ', 1)[0].split()[-1]
    if source.startswith(('tcp:', 'udp:')):
        source = source.split(':', 1)[1]
    if source.startswith('[') and ']:' in source:
        address = source[1:].split(']:', 1)[0]
    else:
        address = source.rsplit(':', 1)[0]
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if not ip.is_global:
        return None
    return int(match.group(1)), match.group(2), ip.compressed


def init_db(db: sqlite3.Connection) -> None:
    db.execute('''CREATE TABLE IF NOT EXISTS observations(
        client_id INTEGER NOT NULL, kind TEXT NOT NULL, ip_tag TEXT NOT NULL,
        bucket INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0, last_seen INTEGER NOT NULL,
        PRIMARY KEY(client_id,kind,ip_tag,bucket))''')
    db.execute('CREATE INDEX IF NOT EXISTS risk_recent ON observations(bucket,client_id,kind)')
    db.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')


def metadata(db: sqlite3.Connection, key: str, default: str = '') -> str:
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def put_metadata(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (key, value))


def access_logging(config: Path = XRAY_CONFIG) -> str:
    data = json.loads(config.read_text(encoding='utf-8'))
    log = data.get('log') or {}
    if not isinstance(log, dict):
        raise RiskError('Unexpected Xray logging configuration')
    value = log.get('access', '')
    if value == 'none':
        raise RiskError('Xray access logging is disabled; no user activity can be inferred')
    if value:
        raise RiskError('Xray access logs are written to a file; inspect that source before enabling collection')
    return 'systemd-journal'


def private_salt(path: Path) -> bytes:
    if path.is_symlink():
        raise RiskError('Risk salt path must not be a symlink')
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(secrets.token_bytes(32))
    value = path.read_bytes()
    if len(value) != 32:
        raise RiskError('Risk salt is invalid')
    os.chmod(path, 0o600)
    return value


def fingerprint(ip: str, key: bytes) -> str:
    return hmac.new(key, ip.encode('ascii'), hashlib.sha256).hexdigest()


def ingest(db: sqlite3.Connection, messages: list[tuple[int, str]], key: bytes,
           now: int | None = None) -> int:
    now = int(time.time()) if now is None else now
    parsed = 0
    for timestamp, message in messages:
        if not now - RETENTION_SECONDS <= timestamp <= now + 60:
            continue
        item = parse_access(message)
        if item is None:
            continue
        cid, kind, address = item
        bucket = timestamp - timestamp % WINDOW_SECONDS
        db.execute('''INSERT INTO observations(client_id,kind,ip_tag,bucket,hits,last_seen)
            VALUES(?,?,?,?,1,?) ON CONFLICT(client_id,kind,ip_tag,bucket)
            DO UPDATE SET hits=hits+1,last_seen=max(last_seen,excluded.last_seen)''',
            (cid, kind, fingerprint(address, key), bucket, timestamp))
        parsed += 1
    db.execute('DELETE FROM observations WHERE bucket<?', (now - RETENTION_SECONDS,))
    return parsed


def collect_journal(db_path: Path = RISK_DB, salt_path: Path = SALT_FILE,
                    config: Path = XRAY_CONFIG, journal=None, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    access_logging(config)
    if db_path.is_symlink() or db_path.parent.is_symlink():
        raise RiskError('Unexpected monitoring database symlink')
    if db_path == RISK_DB:
        directory = db_path.parent
        if not directory.is_dir():
            raise RiskError('Private risk-monitor directory missing; run hostkey-risk-stage.sh')
        info = directory.stat()
        if (info.st_uid != 0 or info.st_gid != grp.getgrnam('caesar-manager').gr_gid
                or info.st_mode & 0o027):
            raise RiskError('Private risk-monitor directory has unsafe permissions')
    db_path.parent.mkdir(parents=True, exist_ok=True)
    key = private_salt(salt_path)
    with sqlite3.connect(db_path, timeout=30) as db:
        init_db(db)
        old_cursor = metadata(db, 'journal_cursor')
    if journal is None:
        args = ['journalctl', '-u', 'xray.service', '--output=json', '--no-pager']
        args += ['--after-cursor=' + old_cursor] if old_cursor else ['--since=-15min']
        result = subprocess.run(args, text=True, capture_output=True, timeout=45, check=False)
        if result.returncode:
            raise RiskError('Cannot read Xray journal: ' + result.stderr[-400:])
        lines = result.stdout.splitlines()
    else:
        lines = journal
    if len(lines) > 100000:
        raise RiskError('Journal backlog too large; refusing partial risk calculations')
    messages = []
    cursor = old_cursor
    for line in lines:
        try:
            record = json.loads(line)
            ts = int(record['__REALTIME_TIMESTAMP']) // 1000000
            text = record.get('MESSAGE', '')
            if isinstance(text, str):
                messages.append((ts, text))
            cursor = record.get('__CURSOR', cursor)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute('BEGIN IMMEDIATE')
        init_db(db)
        count = ingest(db, messages, key, now)
        put_metadata(db, 'last_poll', str(now))
        put_metadata(db, 'total_access_events', str(int(metadata(db, 'total_access_events', '0')) + count))
        if count:
            put_metadata(db, 'last_access_event', str(now))
        if cursor:
            put_metadata(db, 'journal_cursor', cursor)
    os.chmod(db_path, 0o640)
    # The collector is root-owned; only the panel's service group may read
    # derived alerts. The private hashing key stays root-only (0600).
    if db_path == RISK_DB:
        os.chown(db_path, -1, grp.getgrnam('caesar-manager').gr_gid)
    with sqlite3.connect(db_path, timeout=5) as db:
        total = int(metadata(db, 'total_access_events', '0'))
    return {'ok': True, 'source': 'systemd-journal', 'new_events': count, 'journal_entries': len(lines),
            'total_access_events': total,
            'no_vpn_restart': True}


def risk_summary(risk_db: Path = RISK_DB, clients_db: Path = CLIENT_DB,
                 now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    unavailable = {'ready': False, 'message': 'Мониторинг ещё не настроен', 'alerts': [],
                   'updated_at': None}
    if not risk_db.is_file():
        return unavailable
    with sqlite3.connect(f'file:{risk_db}?mode=ro', uri=True, timeout=5) as db:
        last_poll = int(metadata(db, 'last_poll', '0'))
        total = int(metadata(db, 'total_access_events', '0'))
        if now - last_poll > 240:
            return {**unavailable, 'message': 'Мониторинг давно не обновлялся'}
        if not total:
            return {**unavailable, 'message': 'Ожидаем первые данные Xray', 'updated_at': last_poll}
        rows = db.execute('''SELECT client_id,kind,ip_tag,bucket,hits,last_seen FROM observations
                             WHERE bucket>=?''', (now - 86400,)).fetchall()
    with sqlite3.connect(f'file:{clients_db}?mode=ro', uri=True, timeout=5) as db:
        names = dict(db.execute('SELECT id,name FROM clients'))
        has_archives = db.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_client_archives'").fetchone()
        archived = {row[0] for row in db.execute('SELECT client_id FROM caesar_client_archives')} if has_archives else set()
    by_device = defaultdict(list)
    for cid, kind, ip_tag, bucket, hits, last_seen in rows:
        if cid in names and cid not in archived:
            by_device[(cid, kind)].append((ip_tag, bucket, hits, last_seen))
    alerts = []
    for (cid, kind), entries in by_device.items():
        recent = defaultdict(set)
        for ip_tag, bucket, hits, _ in entries:
            if bucket >= now - 3600 and hits >= 4:
                recent[bucket].add(ip_tag)
        overlaps = sum(len(ips) >= 2 for ips in recent.values())
        if overlaps < 3:
            continue
        level = 'high' if overlaps >= 6 else 'review'
        alerts.append({'client_id': cid, 'name': names[cid] or f'Клиент №{cid}',
                       'kind': kind, 'level': level, 'ip_count': len({row[0] for row in entries}),
                       'overlap_windows': overlaps, 'last_seen': max(row[3] for row in entries),
                       'reason': 'Повторная активность одного ключа из разных сетей'})
    alerts.sort(key=lambda item: (item['level'] != 'high', -item['overlap_windows'], item['client_id']))
    return {'ready': True, 'updated_at': last_poll, 'observed_24h': bool(rows), 'alerts': alerts[:30],
            'high': sum(a['level'] == 'high' for a in alerts),
            'review': sum(a['level'] == 'review' for a in alerts),
            'message': 'Мониторинг активен' if rows else 'Мониторинг активен · пока нет новых событий'}


def main() -> None:
    parser = argparse.ArgumentParser(description='Read-only Xray key-sharing activity monitor')
    parser.add_argument('command', choices=['check', 'collect', 'summary'])
    args = parser.parse_args()
    try:
        if args.command == 'check':
            result = {'ok': True, 'source': access_logging(), 'no_vpn_restart': True}
        elif args.command == 'collect':
            result = collect_journal()
        else:
            result = risk_summary()
    except (RiskError, OSError, ValueError, sqlite3.Error) as error:
        parser.exit(2, f'Monitoring unavailable: {error}\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
