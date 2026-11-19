"""No-network tests for HOSTKEY admin Web Push subscriptions and delivery."""

import base64
from datetime import date
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import hostkey_push as push


def encoded(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def subscription(endpoint='https://fcm.googleapis.com/fcm/send/test'):
    return {'endpoint': endpoint, 'keys': {'p256dh': encoded(b'\x04' + b'\x01' * 64),
                                          'auth': encoded(b'\x02' * 16)}}


class HostkeyPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.push_db = Path(self.temp.name) / 'push.db'
        self.clients = Path(self.temp.name) / 'clients.db'
        with sqlite3.connect(self.clients) as db:
            db.execute('CREATE TABLE clients(id INTEGER PRIMARY KEY,name TEXT,period TEXT,end_date TEXT)')
            db.executemany('INSERT INTO clients VALUES(?,?,?,?)', [
                (1, 'Семь дней', '1m', '2026-11-25'),
                (2, 'Три дня', '1m', '2026-11-21'),
                (3, 'Один день', '1m', '2026-11-19'),
                (4, 'Истёк', '1m', '2026-11-18'),
                (5, 'Навсегда', 'lifetime', None),
            ])

    def test_only_supported_push_providers_are_accepted(self):
        self.assertEqual(push.validate_subscription(subscription())['endpoint'], subscription()['endpoint'])
        for url in ('http://127.0.0.1/secret', 'https://example.com/secret',
                    'https://fcm.googleapis.com.evil.net/endpoint',
                    'https://fcm.googleapis.com:8443/endpoint',
                    'https://[bad-host/endpoint'):
            with self.subTest(url=url), self.assertRaises(push.PushError):
                push.validate_subscription(subscription(url))
        bad = subscription()
        bad['keys']['p256dh'] = 'a'
        with self.assertRaises(push.PushError):
            push.validate_subscription(bad)

    def test_device_registration_queue_and_unsubscribe(self):
        ident = push.subscribe(subscription(), self.push_db)
        self.assertEqual(push.subscribe(subscription(), self.push_db), ident)
        ticket = push.queue_test(ident, self.push_db)
        self.assertEqual(push.test_status(ticket, self.push_db), 'pending')
        with self.assertRaises(push.PushError) as caught:
            push.queue_test(ident, self.push_db)
        self.assertEqual(caught.exception.status, 429)
        push.unsubscribe(ident, self.push_db)
        with self.assertRaises(push.PushError):
            push.queue_test(ident, self.push_db)

    def test_subscription_boundaries_follow_moscow_paid_through_day(self):
        today = date(2026, 11, 19)
        self.assertEqual(push.due_kind('1m', '2026-11-25', today), 'seven_days')
        self.assertEqual(push.due_kind('1m', '2026-11-21', today), 'three_days')
        self.assertEqual(push.due_kind('1m', '2026-11-19', today), 'one_day')
        self.assertEqual(push.due_kind('1m', '2026-11-18', today), 'expired')
        self.assertIsNone(push.due_kind('lifetime', None, today))

    def test_delivery_is_durable_once_per_date_and_device(self):
        ident = push.subscribe(subscription(), self.push_db)
        ticket = push.queue_test(ident, self.push_db)
        sent = []
        def fake_send(_subscription, payload, _private, _sender):
            sent.append(payload)
        with patch.object(push, 'send', side_effect=fake_send):
            first = push.deliver(self.push_db, self.clients, today=date(2026, 11, 19))
            second = push.deliver(self.push_db, self.clients, today=date(2026, 11, 19))
        self.assertEqual(first['sent'], 4)
        self.assertEqual(first['test_sent'], 1)
        self.assertEqual(second['sent'], 0)
        self.assertEqual(push.test_status(ticket, self.push_db), 'sent')
        self.assertEqual(len(sent), 5)
        self.assertFalse(any('Навсегда' in item['body'] for item in sent))
        with sqlite3.connect(self.clients) as db:
            db.execute("UPDATE clients SET end_date='2026-12-01' WHERE id=4")
        with patch.object(push, 'send', side_effect=fake_send):
            third = push.deliver(self.push_db, self.clients, today=date(2026, 11, 25))
        self.assertGreaterEqual(third['sent'], 1)

    def test_archived_customers_do_not_generate_alerts(self):
        with sqlite3.connect(self.clients) as db:
            db.execute('CREATE TABLE caesar_client_archives(client_id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO caesar_client_archives VALUES(4)')
        self.assertNotIn(4, [item['id'] for item in push.client_snapshot(self.clients)])

    def test_high_risk_sends_one_push_per_key_per_day_without_blocking(self):
        push.subscribe(subscription(), self.push_db)
        now = time.time()
        suspicious = {'ready': True, 'alerts': [
            {'client_id': 42, 'kind': 'phone', 'level': 'high', 'last_seen': now},
            {'client_id': 43, 'kind': 'pc', 'level': 'review', 'last_seen': now},
            {'client_id': 44, 'kind': 'phone', 'level': 'high', 'last_seen': now - 3600}]}
        sent = []
        with patch.object(push.hostkey_risk, 'risk_summary', return_value=suspicious), \
                patch.object(push, 'send', side_effect=lambda _s, payload, _p, _sender: sent.append(payload)):
            first = push.deliver(self.push_db, self.clients, today=date(2026, 11, 19))
            second = push.deliver(self.push_db, self.clients, today=date(2026, 11, 19))
        self.assertEqual(first['risk_sent'], 1)
        self.assertEqual(second['risk_sent'], 0)
        self.assertEqual(len([item for item in sent if 'риск' in item['body']]), 1)


if __name__ == '__main__':
    unittest.main()
