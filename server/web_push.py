"""Authenticated device subscriptions and durable Web Push delivery. No VPN writes."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import jsonify, request

DEFAULT_DB = '/var/lib/vpn-manager-production-auth/push.db'
DEFAULT_KEY = '/etc/vpn-manager-push/private.pem'
ALLOWED_HOSTS = {'web.push.apple.com', 'fcm.googleapis.com', 'updates.push.services.mozilla.com'}
KINDS = {'seven_days': '7 дней', 'three_days': '3 дня', 'one_day': '1 день', 'expired': 'истёк срок'}


def decode_key(value, length):
    if not isinstance(value, str) or len(value) > 150:
        raise ValueError('Invalid subscription key')
    try:
        data = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except (ValueError, TypeError):
        raise ValueError('Invalid subscription key') from None
    if len(data) != length:
        raise ValueError('Invalid subscription key length')
    return data


def validate_subscription(data):
    if not isinstance(data, dict) or not isinstance(data.get('endpoint'), str):
        raise ValueError('Invalid subscription')
    endpoint = data['endpoint']
    url = urlsplit(endpoint)
    host = url.hostname or ''
    allowed = host in ALLOWED_HOSTS or host.endswith('.notify.windows.com')
    if (len(endpoint) > 2048 or url.scheme != 'https' or not allowed or
            url.username or url.password or url.port not in (None, 443) or url.fragment or
            not url.path or url.path == '/' or any(ord(c) < 33 for c in endpoint)):
        raise ValueError('Unsupported push provider')
    keys = data.get('keys')
    if not isinstance(keys, dict):
        raise ValueError('Missing subscription keys')
    public = decode_key(keys.get('p256dh'), 65)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    decode_key(keys.get('auth'), 16)
    return dict(endpoint=endpoint, keys=dict(p256dh=keys['p256dh'], auth=keys['auth']))


def connect(path):
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=15000')
    con.execute('CREATE TABLE IF NOT EXISTS push_devices(id TEXT PRIMARY KEY, subscription TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL, test_at REAL NOT NULL DEFAULT 0)')
    con.execute('CREATE TABLE IF NOT EXISTS push_deliveries(device_id TEXT NOT NULL, event_key TEXT NOT NULL, sent_at REAL, attempted_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(device_id,event_key))')
    return con


def device_id(subscription):
    return hashlib.sha256(subscription['endpoint'].encode()).hexdigest()


def public_key(private_path):
    key = serialization.load_pem_private_key(Path(private_path).read_bytes(), password=None)
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def current_kind(client, now=None):
    if client.get('period') == 'lifetime' or not client.get('end_date'):
        return None
    raw = client['end_date']
    try:
        end = datetime.fromisoformat(raw.replace('Z', '+00:00') if 'T' in raw else raw + 'T00:00:00+03:00')
        if end.tzinfo is None:
            return None
        delta = end.timestamp() - (time.time() if now is None else now)
    except (ValueError, TypeError, OverflowError):
        return None
    return 'expired' if delta <= 0 else 'one_day' if delta <= 86400 else 'three_days' if delta <= 259200 else 'seven_days' if delta <= 604800 else None


def payload(client, kind):
    name = str(client.get('name') or ('Клиент #' + str(client['id'])))[:100]
    body = name + ': ' + ('подписка закончилась' if kind == 'expired' else 'осталось ' + KINDS[kind])
    return dict(title='VPN MANAGER', body=body, tag='subscription-' + str(client['id']), url='/panel-live/')


def send(subscription, message, key_path, sender=None):
    from pywebpush import webpush
    sender = sender or webpush
    # Never follow redirects from an external subscription endpoint.
    import requests
    session = requests.Session()
    original = session.post
    session.post = lambda *a, **kw: original(*a, **dict(kw, allow_redirects=False))
    return sender(subscription_info=subscription, data=json.dumps(message, ensure_ascii=False),
                  vapid_private_key=key_path,
                  vapid_claims={'sub': 'https://185-23-239-142.sslip.io:8444/panel-live/'},
                  ttl=3600, timeout=10, requests_session=session)


def deliver(path, key_path, clients, sender=None, now=None):
    now = time.time() if now is None else now
    counts = dict(sent=0, failed=0, removed=0)
    with connect(path) as con:
        devices = con.execute('SELECT * FROM push_devices WHERE active=1').fetchall()
    for client in clients:
        kind = current_kind(client, now)
        if not kind:
            continue
        key = hashlib.sha256(json.dumps([client['id'], kind, client['end_date']]).encode()).hexdigest()
        for device in devices:
            with connect(path) as con:
                con.execute('BEGIN IMMEDIATE')
                active = con.execute('SELECT active FROM push_devices WHERE id=?', (device['id'],)).fetchone()
                if not active or not active['active']:
                    continue
                con.execute('INSERT OR IGNORE INTO push_deliveries(device_id,event_key) VALUES(?,?)', (device['id'], key))
                row = con.execute('SELECT * FROM push_deliveries WHERE device_id=? AND event_key=?', (device['id'], key)).fetchone()
                if row['sent_at'] is not None or row['attempts'] >= 8 or (row['attempts'] and now - row['attempted_at'] < 900):
                    continue
                con.execute('UPDATE push_deliveries SET attempts=attempts+1,attempted_at=? WHERE device_id=? AND event_key=?', (now, device['id'], key))
            try:
                subscription = validate_subscription(json.loads(device['subscription']))
                send(subscription, payload(client, kind), key_path, sender)
            except Exception as error:
                status = getattr(getattr(error, 'response', None), 'status_code', None)
                with connect(path) as con:
                    if status in (404, 410):
                        con.execute('UPDATE push_devices SET active=0 WHERE id=?', (device['id'],))
                        counts['removed'] += 1
                counts['failed'] += 1
            else:
                with connect(path) as con:
                    con.execute('UPDATE push_deliveries SET sent_at=? WHERE device_id=? AND event_key=?', (now, device['id'], key))
                counts['sent'] += 1
    return counts


def register(app, authorized):
    path = app.config.get('PUSH_DB', os.environ.get('PANEL_PUSH_DB', DEFAULT_DB))
    key_path = app.config.get('PUSH_KEY', os.environ.get('PANEL_PUSH_KEY', DEFAULT_KEY))

    @app.get('/api/push/key')
    @authorized
    def push_key():
        try:
            key = public_key(key_path)
        except (OSError, ValueError):
            return jsonify(ok=False, error='Push service not configured'), 503
        return jsonify(ok=True, public_key=key)

    @app.post('/api/push/subscribe')
    @authorized
    def push_subscribe():
        try:
            subscription = validate_subscription(request.get_json(silent=True))
        except (ValueError, TypeError):
            return jsonify(ok=False, error='Invalid or unsupported push subscription'), 400
        now = time.time()
        ident = device_id(subscription)
        with connect(path) as con:
            if con.execute('SELECT count(*) FROM push_devices WHERE active=1').fetchone()[0] >= 20 and not con.execute('SELECT 1 FROM push_devices WHERE id=?', (ident,)).fetchone():
                return jsonify(ok=False, error='Device limit reached'), 409
            con.execute('INSERT INTO push_devices(id,subscription,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET subscription=excluded.subscription,active=1,updated_at=excluded.updated_at', (ident, json.dumps(subscription), now, now))
        return jsonify(ok=True, device_id=ident)

    @app.post('/api/push/unsubscribe')
    @authorized
    def push_unsubscribe():
        data = request.get_json(silent=True) or {}
        with connect(path) as con:
            con.execute('UPDATE push_devices SET active=0 WHERE id=?', (str(data.get('device_id', ''))[:64],))
        return jsonify(ok=True)

    @app.post('/api/push/test')
    @authorized
    def push_test():
        data = request.get_json(silent=True) or {}
        ident = str(data.get('device_id', ''))[:64]
        now = time.time()
        with connect(path) as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM push_devices WHERE id=? AND active=1', (ident,)).fetchone()
            if not row:
                return jsonify(ok=False, error='Device subscription not found'), 404
            if now - row['test_at'] < 30:
                return jsonify(ok=False, error='Wait 30 seconds before another test'), 429
            con.execute('UPDATE push_devices SET test_at=? WHERE id=?', (now, ident))
        try:
            send(validate_subscription(json.loads(row['subscription'])), dict(title='VPN MANAGER', body='Уведомления включены. Напомним за 7, 3 и 1 день до окончания подписки.', tag='vpn-push-test', url='/panel-live/'), key_path, app.config.get('PUSH_SENDER'))
        except Exception:
            return jsonify(ok=False, error='Push delivery failed; retry later'), 503
        return jsonify(ok=True)


def main():
    # Runs as vpn-panel, isolated from VPN files and keys. Read-only bridge calls.
    import requests
    path = os.environ.get('PANEL_PUSH_DB', DEFAULT_DB)
    key = os.environ.get('PANEL_PUSH_KEY', DEFAULT_KEY)
    headers = {'X-VPN-Token': os.environ['PANEL_AWG_TOKEN']}
    url = os.environ.get('PANEL_AWG_URL', 'http://127.0.0.1:5055').rstrip('/')
    result = requests.get(url + '/api/managed/clients', headers=headers, timeout=20)
    result.raise_for_status()
    data = result.json()
    if not data.get('ok') or not isinstance(data.get('clients'), list):
        raise SystemExit('Client snapshot unavailable')
    report = deliver(path, key, data['clients'])
    print(json.dumps(report))  # No endpoints, tokens, names, or private keys in logs.


if __name__ == '__main__':
    main()
