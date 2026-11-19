"""Read-only, privacy preserving client VPN risk monitoring tests."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from server import hostkey_risk as risk


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.risk_db = base / 'risk.db'
        self.salt = base / 'salt.key'
        self.config = base / 'config.json'
        self.clients = base / 'clients.db'
        self.config.write_text('{"log":{"loglevel":"warning"}}')
        with sqlite3.connect(self.clients) as db:
            db.executescript('''CREATE TABLE clients(id INTEGER PRIMARY KEY,name TEXT);
                INSERT INTO clients VALUES(42,'Тестовый 42');
                INSERT INTO clients VALUES(43,'Другой 43');
                CREATE TABLE caesar_client_archives(client_id INTEGER PRIMARY KEY);
                INSERT INTO caesar_client_archives VALUES(43);''')
        self.now = 1795080000

    @staticmethod
    def log(ip, cid=42, kind='phone'):
        return (f'2026/11/19 12:00:00 {ip}:52123 accepted tcp:example.org:443 '
                f'[socks >> direct] email: caesar-{cid}-{kind}')

    def journal(self, samples):
        return [json.dumps({'__REALTIME_TIMESTAMP': str(timestamp * 1000000),
                            '__CURSOR': f'sample-{index}', 'MESSAGE': message})
                for index, (timestamp, message) in enumerate(samples)]

    def collect(self, samples):
        return risk.collect_journal(self.risk_db, self.salt, self.config,
                                    journal=self.journal(samples), now=self.now)

    def test_parse_requires_public_source_and_managed_key(self):
        self.assertEqual(risk.parse_access(self.log('8.8.8.8')), (42, 'phone', '8.8.8.8'))
        self.assertEqual(risk.parse_access(self.log('[2606:4700:4700::1111]', kind='pc')),
                         (42, 'pc', '2606:4700:4700::1111'))
        self.assertIsNone(risk.parse_access(self.log('127.0.0.1')))
        self.assertIsNone(risk.parse_access(self.log('10.0.0.8')))
        self.assertIsNone(risk.parse_access(self.log('192.0.2.1')))
        self.assertIsNone(risk.parse_access(self.log('8.8.8.8').replace('accepted', 'rejected')))
        self.assertIsNone(risk.parse_access(self.log('8.8.8.8').replace('caesar-', 'test-')))

    def test_disabled_file_logging_cannot_silently_fake_good_status(self):
        self.config.write_text('{"log":{"access":"none"}}')
        with self.assertRaises(risk.RiskError):
            self.collect([])
        self.config.write_text('{"log":{"access":"/var/log/xray/access.log"}}')
        with self.assertRaises(risk.RiskError):
            self.collect([])
        self.assertFalse(self.risk_db.exists())

    def test_state_unknown_until_real_public_source_event_arrives(self):
        self.assertFalse(risk.risk_summary(self.risk_db, self.clients, self.now)['ready'])
        self.collect([(self.now, self.log('127.0.0.1'))])
        info = risk.risk_summary(self.risk_db, self.clients, self.now)
        self.assertFalse(info['ready'])
        self.assertIn('Ожидаем', info['message'])
        self.collect([(self.now, self.log('8.8.8.8'))])
        info = risk.risk_summary(self.risk_db, self.clients, self.now)
        self.assertTrue(info['ready'])
        self.assertEqual(info['alerts'], [])
        self.assertFalse(risk.risk_summary(self.risk_db, self.clients, self.now + 301)['ready'])

    def test_sustained_same_key_overlaps_create_alert_without_storage_of_raw_ips(self):
        events = []
        for bucket in range(6):
            when = self.now - 1800 + bucket * 300 + 10
            for _ in range(4):
                events.extend([(when, self.log('8.8.8.8')), (when, self.log('1.1.1.1'))])
        result = self.collect(events)
        self.assertEqual(result['new_events'], 48)
        summary = risk.risk_summary(self.risk_db, self.clients, self.now)
        self.assertEqual(summary['high'], 1)
        self.assertEqual(summary['alerts'][0]['client_id'], 42)
        self.assertEqual(summary['alerts'][0]['level'], 'high')
        raw = self.risk_db.read_bytes()
        self.assertNotIn(b'8.8.8.8', raw)
        self.assertNotIn(b'1.1.1.1', raw)
        self.assertNotIn(b'example.org', raw)
        self.assertEqual(self.salt.stat().st_mode & 0o777, 0o600)

    def test_distinct_keys_and_archived_clients_not_flagged(self):
        events = []
        for bucket in range(7):
            when = self.now - 2200 + bucket * 300 + 10
            for _ in range(4):
                events.extend([(when, self.log('8.8.8.8', kind='phone')),
                               (when, self.log('1.1.1.1', kind='pc')),
                               (when, self.log('8.8.8.8', cid=43)),
                               (when, self.log('1.1.1.1', cid=43))])
        self.collect(events)
        self.assertEqual(risk.risk_summary(self.risk_db, self.clients, self.now)['alerts'], [])

    def test_short_network_switch_does_not_trigger_warning(self):
        events = [(self.now - 240, self.log('8.8.8.8'))] * 7
        events += [(self.now - 10, self.log('1.1.1.1'))] * 7
        self.collect(events)
        self.assertEqual(risk.risk_summary(self.risk_db, self.clients, self.now)['alerts'], [])


if __name__ == '__main__':
    unittest.main()
