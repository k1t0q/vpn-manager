"""CAESAR HOSTKEY admin Web Push: durable queue and read-only billing alerts.

Panel routes only update a local SQLite queue.  A separately sandboxed timer
delivers HTTPS Web Push; the VPN panel retains localhost-only network access.
"""

from __future__ import annotations

import argparse
import base64
from datetime import date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

try:
    from .xray_hostkey import MOSCOW
except ImportError:
    from xray_hostkey import MOSCOW


PUSH_DB = Path(os.environ.get('CAESAR_PUSH_DB', '/var/lib/caesar-manager/push.db'))
CLIENT_DB = Path(os.environ.get('CAESAR_DB', '/var/lib/caesar-manager/clients.db'))
PUBLIC_KEY = Path(os.environ.get('CAESAR_PUSH_PUBLIC', '/var/lib/caesar-manager/push-public.key'))
PRIVATE_KEY = Path(os.environ.get('CAESAR_PUSH_PRIVATE', '/var/lib/caesar-manager/push-private.pem'))
ALLOWED_HOSTS = {'web.push.apple.com', 'fcm.googleapis.com', 'updates.push.services.mozilla.com'}
DEVICE_LIMIT = 12
ID_RE = re.compile(r'^[a-f0-9]{64}$')
TICKET_RE = re.compile(r'^[a-f0-9]{32}$')
REMINDERS = {'seven_days': '7 дней', 'three_days': '3 дня', 'one_day': '1 день'}


class PushError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _decode_key(value: str, length: int) -> bytes:
    if not isinstance(value, str) or len(value) > 150:
        raise PushError('Некорректный ключ устройства')
    try:
        decoded = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except (ValueError, TypeError):
        raise PushError('Некорректный ключ устройства') from None
    if len(decoded) != length:
        raise PushError('Некорректная длина ключа')
    return decoded


def validate_subscription(value: dict) -> dict:
    """Reject unexpected push origins, including arbitrary HTTPS SSRF targets."""
    if not isinstance(value, dict) or not isinstance(value.get('endpoint'), str):
        raise PushError('Некорректная push-подписка')
    endpoint = value['endpoint']
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ''
        port = url.port
    except ValueError:
        raise PushError('Некорректный адрес push-сервиса') from None
    allowed = host in ALLOWED_HOSTS or host.endswith('.notify.windows.com')
    if (len(endpoint) > 2048 or url.scheme != 'https' or not allowed or port not in (None, 443)
            or url.username or url.password or url.fragment or url.path in ('', '/')
            or any(ord(char) < 33 for char in endpoint)):
        raise PushError('Неподдерживаемый push-сервис')
    keys = value.get('keys')
    if not isinstance(keys, dict):
        raise PushError('Не указаны ключи push-подписки')
    public = _decode_key(keys.get('p256dh'), 65)
    if public[0] != 4:
        raise PushError('Некорректный ключ устройства')
    _decode_key(keys.get('auth'), 16)
    return {'endpoint': endpoint, 'keys': {'p256dh': keys['p256dh'], 'auth': keys['auth']}}


def connect(path: Path = PUSH_DB) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=15000')
    db.execute('''CREATE TABLE IF NOT EXISTS devices(
        id TEXT PRIMARY KEY, subscription TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        created REAL NOT NULL, updated REAL NOT NULL, last_test_requested REAL NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS deliveries(
        device_id TEXT NOT NULL, event_key TEXT NOT NULL, attempted REAL NOT NULL DEFAULT 0,
        sent REAL, attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(device_id,event_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS tests(
        ticket TEXT PRIMARY KEY, device_id TEXT NOT NULL, requested REAL NOT NULL,
        attempted REAL NOT NULL DEFAULT 0, sent REAL, attempts INTEGER NOT NULL DEFAULT 0)''')
    db.commit()
    return db


def public_key(path: Path = PUBLIC_KEY) -> str:
    value = path.read_text(encoding='ascii').strip()
    if len(_decode_key(value, 65)) != 65 or not value:
        raise PushError('Push-ключ ещё не настроен', 503)
    return value


def subscribe(subscription: dict, path: Path = PUSH_DB) -> str:
    data = validate_subscription(subscription)
    ident = sha256(data['endpoint'].encode()).hexdigest()
    now = time.time()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        exists = db.execute('SELECT active FROM devices WHERE id=?', (ident,)).fetchone()
        count = db.execute('SELECT count(*) FROM devices WHERE active=1').fetchone()[0]
        if count >= DEVICE_LIMIT and (not exists or not exists['active']):
            raise PushError('Достигнут лимит устройств для уведомлений', 409)
        db.execute('''INSERT INTO devices(id,subscription,created,updated) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET subscription=excluded.subscription,active=1,
            updated=excluded.updated''', (ident, json.dumps(data), now, now))
    return ident


def unsubscribe(ident: str, path: Path = PUSH_DB) -> None:
    if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
        raise PushError('Некорректное устройство')
    with connect(path) as db:
        db.execute('UPDATE devices SET active=0 WHERE id=?', (ident,))


def queue_test(ident: str, path: Path = PUSH_DB) -> str:
    if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
        raise PushError('Некорректное устройство')
    now = time.time()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        device = db.execute('SELECT last_test_requested FROM devices WHERE id=? AND active=1', (ident,)).fetchone()
        if device is None:
            raise PushError('Сначала включите уведомления', 404)
        if now - device['last_test_requested'] < 30:
            raise PushError('Повторная проверка доступна через 30 секунд', 429)
        ticket = secrets.token_hex(16)
        db.execute('UPDATE devices SET last_test_requested=? WHERE id=?', (now, ident))
        db.execute('INSERT INTO tests(ticket,device_id,requested) VALUES(?,?,?)', (ticket, ident, now))
    return ticket


def test_status(ticket: str, path: Path = PUSH_DB) -> str:
    if not isinstance(ticket, str) or not TICKET_RE.fullmatch(ticket):
        raise PushError('Некорректная проверка')
    with connect(path) as db:
        row = db.execute('SELECT sent,attempts FROM tests WHERE ticket=?', (ticket,)).fetchone()
    if row is None:
        raise PushError('Проверка не найдена', 404)
    return 'sent' if row['sent'] else 'failed' if row['attempts'] >= 3 else 'pending'


def due_kind(period: str | None, end_date: str | None, today: date) -> str | None:
    if period == 'lifetime' or not end_date:
        return None
    try:
        remaining = (date.fromisoformat(end_date[:10]) - today).days + 1
    except (TypeError, ValueError):
        return None
    if remaining <= 0:
        return 'expired'
    if remaining <= 1:
        return 'one_day'
    if remaining <= 3:
        return 'three_days'
    if remaining <= 7:
        return 'seven_days'
    return None


def client_snapshot(path: Path = CLIENT_DB) -> list[dict]:
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        archive = db.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_client_archives'").fetchone()
        exclusion = 'WHERE id NOT IN (SELECT client_id FROM caesar_client_archives)' if archive else ''
        rows = db.execute('SELECT id,name,period,end_date FROM clients ' + exclusion).fetchall()
    return [dict(row) for row in rows]


def send(subscription: dict, payload: dict, private: Path = PRIVATE_KEY, sender=None) -> None:
    if sender is None:
        from pywebpush import webpush
        sender = webpush
    import requests
    session = requests.Session()
    original_post = session.post
    session.post = lambda *a, **kw: original_post(*a, **dict(kw, allow_redirects=False))
    sender(subscription_info=validate_subscription(subscription), data=json.dumps(payload, ensure_ascii=False),
           vapid_private_key=str(private), vapid_claims={'sub': 'https://caesarbot.ru'},
           ttl=3600, timeout=10, requests_session=session)


def _try_send(path: Path, ident: str, event_key: str, subscription: dict, payload: dict,
              private: Path, sender, now: float) -> str:
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute('SELECT 1 FROM devices WHERE id=? AND active=1', (ident,)).fetchone()
        if not active:
            return 'skipped'
        db.execute('INSERT OR IGNORE INTO deliveries(device_id,event_key) VALUES(?,?)', (ident, event_key))
        row = db.execute('SELECT attempted,sent,attempts FROM deliveries WHERE device_id=? AND event_key=?',
                         (ident, event_key)).fetchone()
        if row['sent'] or row['attempts'] >= 3 or (row['attempts'] and now - row['attempted'] < 900):
            return 'skipped'
        db.execute('UPDATE deliveries SET attempts=attempts+1,attempted=? WHERE device_id=? AND event_key=?',
                   (now, ident, event_key))
    try:
        send(subscription, payload, private, sender)
    except Exception as error:
        code = getattr(getattr(error, 'response', None), 'status_code', None)
        if code in (404, 410):
            with connect(path) as db:
                db.execute('UPDATE devices SET active=0 WHERE id=?', (ident,))
        return 'failed'
    with connect(path) as db:
        db.execute('UPDATE deliveries SET sent=? WHERE device_id=? AND event_key=?', (now, ident, event_key))
    return 'sent'


def deliver(push_path: Path = PUSH_DB, clients_path: Path = CLIENT_DB,
            private: Path = PRIVATE_KEY, sender=None, today: date | None = None) -> dict:
    now = time.time()
    today = today or datetime.now(MOSCOW).date()
    clients = client_snapshot(clients_path)
    with connect(push_path) as db:
        devices = db.execute('SELECT id,subscription FROM devices WHERE active=1').fetchall()
        tests = db.execute('''SELECT t.ticket,t.device_id,d.subscription,t.attempted,t.attempts
            FROM tests t JOIN devices d ON d.id=t.device_id AND d.active=1 WHERE t.sent IS NULL
            AND t.attempts<3''').fetchall()
    result = {'sent': 0, 'failed': 0, 'test_sent': 0, 'test_failed': 0}
    for record in tests:
        if record['attempts'] and now - record['attempted'] < 60:
            continue
        with connect(push_path) as db:
            db.execute('UPDATE tests SET attempted=?,attempts=attempts+1 WHERE ticket=?', (now, record['ticket']))
        try:
            send(json.loads(record['subscription']), {'title': 'CAESAR VPN MANAGER',
                'body': 'Проверка успешна! Уведомления о сроках подписок включены.',
                'tag': 'caesar-push-test'}, private, sender)
        except Exception:
            result['test_failed'] += 1
        else:
            with connect(push_path) as db:
                db.execute('UPDATE tests SET sent=? WHERE ticket=?', (now, record['ticket']))
            result['test_sent'] += 1
    for client in clients:
        kind = due_kind(client['period'], client['end_date'], today)
        if not kind:
            continue
        name = str(client.get('name') or f"Клиент №{client['id']}")[:80]
        message = f'{name}: подписка закончилась' if kind == 'expired' else f'{name}: осталось {REMINDERS[kind]}'
        payload = {'title': 'CAESAR VPN MANAGER', 'body': message, 'tag': f"caesar-client-{client['id']}"}
        event_key = sha256(f"{client['id']}:{client['end_date']}:{kind}".encode()).hexdigest()
        for device in devices:
            outcome = _try_send(push_path, device['id'], event_key,
                                json.loads(device['subscription']), payload, private, sender, now)
            if outcome == 'sent':
                result['sent'] += 1
            elif outcome == 'failed':
                result['failed'] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description='CAESAR HOSTKEY notification sender')
    parser.add_argument('command', choices=['check', 'deliver'])
    args = parser.parse_args()
    public_key()
    if not PRIVATE_KEY.is_file():
        raise SystemExit('Private VAPID key missing')
    if args.command == 'check':
        client_snapshot()
        with connect() as db:
            print(json.dumps({'ok': True, 'devices': db.execute('SELECT count(*) FROM devices WHERE active=1').fetchone()[0]}))
        return
    print(json.dumps(deliver(), ensure_ascii=False))


if __name__ == '__main__':
    main()
