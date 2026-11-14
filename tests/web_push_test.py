import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from flask import Flask, jsonify, request
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import web_push as push


def encoded(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


class PushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'push.db')
        self.key = str(Path(self.tmp.name) / 'private.pem')
        key = ec.generate_private_key(ec.SECP256R1())
        Path(self.key).write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.sub = dict(endpoint='https://web.push.apple.com/Qtest', keys=dict(p256dh=encoded(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)), auth=encoded(b'a' * 16)))
        self.sender = Mock()
        app = Flask(__name__)
        app.config.update(PUSH_DB=self.db, PUSH_KEY=self.key, PUSH_SENDER=self.sender)
        def authorized(fn):
            def wrapper(*a, **kw):
                if request.headers.get('Authorization') != 'Bearer test':
                    return jsonify(ok=False), 401
                return fn(*a, **kw)
            wrapper.__name__ = fn.__name__
            return wrapper
        push.register(app, authorized)
        self.client = app.test_client()
        self.headers = {'Authorization': 'Bearer test'}
        self.now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc).timestamp()

    def subscribe(self):
        return self.client.post('/api/push/subscribe', json=self.sub, headers=self.headers).json['device_id']

    def record(self, days, **kw):
        return dict(id=5, name='Test', period='1m', end_date=datetime.fromtimestamp(self.now + days * 86400, timezone.utc).isoformat(), **kw)

    def test_all_routes_require_login(self):
        self.assertEqual(self.client.get('/api/push/key').status_code, 401)
        for route in ['subscribe', 'unsubscribe', 'test']:
            self.assertEqual(self.client.post('/api/push/' + route, json={}).status_code, 401)

    def test_key_is_public_and_stable(self):
        key = self.client.get('/api/push/key', headers=self.headers).json['public_key']
        self.assertEqual(len(push.decode_key(key, 65)), 65)
        self.assertEqual(key, push.public_key(self.key))
        self.assertNotIn('PRIVATE', key)

    def test_rejects_ssrf_and_bad_keys(self):
        for endpoint in ['http://web.push.apple.com/Qtest', 'https://127.0.0.1/x', 'https://web.push.apple.com.evil.test/x', 'https://web.push.apple.com@evil.test/x', 'https://web.push.apple.com:5055/x', 'https://localhost/x', 'file:///etc/passwd']:
            response = self.client.post('/api/push/subscribe', json=dict(self.sub, endpoint=endpoint), headers=self.headers)
            self.assertEqual(response.status_code, 400, endpoint)
        self.assertEqual(self.client.post('/api/push/subscribe', json=dict(self.sub, keys={'p256dh': 'invalid', 'auth': 'invalid'}), headers=self.headers).status_code, 400)

    def test_subscription_upsert_and_test_rate_limit(self):
        ident = self.subscribe()
        self.assertEqual(ident, self.subscribe())
        with push.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM push_devices').fetchone()[0], 1)
        response = self.client.post('/api/push/test', json={'device_id': ident}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/push/test', json={'device_id': ident}, headers=self.headers).status_code, 429)
        self.sender.assert_called_once()
        self.assertEqual(self.sender.call_args.kwargs['timeout'], 10)

    def test_windows_moscow_midnight_and_lifetime(self):
        for days, kind in [(8, None), (7, 'seven_days'), (3, 'three_days'), (1, 'one_day'), (0, 'expired')]:
            self.assertEqual(push.current_kind(self.record(days), self.now), kind)
        self.assertIsNone(push.current_kind(dict(period='lifetime', end_date='2000-01-01'), self.now))
        self.assertEqual(push.current_kind(dict(period='1m', end_date='2026-11-14'), self.now), 'one_day')

    def test_delivery_dedup_and_new_renewal_cycle(self):
        self.subscribe()
        client = self.record(7)
        self.assertEqual(push.deliver(self.db, self.key, [client], self.sender, self.now)['sent'], 1)
        self.assertEqual(push.deliver(self.db, self.key, [client], self.sender, self.now)['sent'], 0)
        for elapsed, kind in [(4, 'three_days'), (6, 'one_day'), (7, 'expired')]:
            self.assertEqual(push.deliver(self.db, self.key, [client], self.sender, self.now + elapsed * 86400)['sent'], 1)
        renewed = self.record(30)
        self.assertEqual(push.deliver(self.db, self.key, [renewed], self.sender, self.now + 23 * 86400)['sent'], 1)

    def test_failure_retries_after_backoff_without_marking_delivered(self):
        self.subscribe()
        self.sender.side_effect = RuntimeError('secret endpoint must not escape')
        self.assertEqual(push.deliver(self.db, self.key, [self.record(2)], self.sender, self.now)['failed'], 1)
        self.assertEqual(push.deliver(self.db, self.key, [self.record(2)], self.sender, self.now + 10)['failed'], 0)
        self.sender.side_effect = None
        self.assertEqual(push.deliver(self.db, self.key, [self.record(2)], self.sender, self.now + 901)['sent'], 1)

    def test_gone_endpoint_deactivated_and_unsubscribe_stops_delivery(self):
        ident = self.subscribe()
        error = RuntimeError()
        error.response = Mock(status_code=410)
        self.sender.side_effect = error
        self.assertEqual(push.deliver(self.db, self.key, [self.record(2)], self.sender, self.now)['removed'], 1)
        self.sender.reset_mock()
        push.deliver(self.db, self.key, [self.record(1)], self.sender, self.now)
        self.sender.assert_not_called()
        self.subscribe()
        self.client.post('/api/push/unsubscribe', json={'device_id': ident}, headers=self.headers)
        push.deliver(self.db, self.key, [self.record(1)], self.sender, self.now)
        self.sender.assert_not_called()

    def test_delivery_is_per_device(self):
        self.subscribe()
        self.sub['endpoint'] += '2'
        self.subscribe()
        self.assertEqual(push.deliver(self.db, self.key, [self.record(3)], self.sender, self.now)['sent'], 2)

    def test_real_webpush_encryption_round_trip_without_network(self):
        import http_ece
        session = Mock()
        session.post.return_value = Mock(status_code=201, text='', headers={})
        transport = session.post
        message = dict(title='VPN MANAGER', body='Тест уведомления')
        with patch('requests.Session', return_value=session):
            push.send(self.sub, message, self.key)
        kwargs = transport.call_args.kwargs
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['headers']['content-encoding'], 'aes128gcm')
        private = serialization.load_pem_private_key(Path(self.key).read_bytes(), password=None)
        clear = http_ece.decrypt(kwargs['data'], private_key=private, auth_secret=b'a' * 16)
        self.assertEqual(json.loads(clear), message)
        self.assertNotIn(message['body'].encode(), kwargs['data'])


if __name__ == '__main__':
    unittest.main()
