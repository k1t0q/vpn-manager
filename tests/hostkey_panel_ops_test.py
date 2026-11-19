"""No network/systemctl integration; exercise SQLite operations and guardrails."""

from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from server.hostkey_panel_ops import PanelOperations, OperationError, add_months
from server.hostkey_panel import Panel


class PanelOperationsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / 'clients.db'
        with sqlite3.connect(self.db) as con:
            con.executescript('''
                CREATE TABLE clients(id INTEGER PRIMARY KEY, user_id TEXT, name TEXT,
                    phone TEXT, start_date TEXT, end_date TEXT, period TEXT,
                    note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    referrer_id INTEGER, wg_client_id TEXT);
                CREATE TABLE client_history(id INTEGER PRIMARY KEY, user_id TEXT,
                    client_id INTEGER, event_type TEXT, description TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO clients(id,user_id,name,phone,start_date,end_date,period,note)
                  VALUES(93,'owner','тест','','2026-11-01','2026-12-01','1m','');
                INSERT INTO clients(id,user_id,name,phone,start_date,end_date,period,note)
                  VALUES(33,'owner','me','','2026-11-01',NULL,'lifetime','');
            ''')
        self.ops = PanelOperations(self.db, frozenset({93}), backup_dir=self.root / 'backups')

    def run_action(self, action, cid, data=None, request_id=None):
        import uuid
        return self.ops.execute(action, cid, data if data is not None else {}, request_id or uuid.uuid4().hex)

    def test_read_only_default_never_mutates(self):
        ops = PanelOperations(self.db, backup_dir=self.root/'backups')
        with self.assertRaises(OperationError) as error:
            ops.execute('extend', 93, {'period':'1m'}, 'idempotent-readonly-111')
        self.assertEqual(error.exception.status, 403)
        self.assertFalse((self.root/'backups').exists())

    def test_only_allowlisted_test_client_can_be_changed(self):
        self.assertEqual(self.ops.permitted_ids(), {93})
        with self.assertRaises(OperationError) as error:
            self.run_action('extend', 33, {'period':'1m'})
        self.assertEqual(error.exception.status, 403)
        self.assertFalse((self.root/'backups').exists())
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT period FROM clients WHERE id=33').fetchone()[0], 'lifetime')

    def test_create_must_be_test_named_and_provisions_both_devices(self):
        with self.assertRaises(OperationError):
            self.run_action('create', None, {'name':'Real Client','period':'1m'})
        created = self.run_action('create', None, {'name':'TEST Device','period':'1m','phone':''})
        self.assertTrue(created['ok'])
        new_id = created['id']
        self.assertIn(new_id, self.ops.permitted_ids())
        with sqlite3.connect(self.db) as con:
            rows = con.execute('SELECT kind,manually_enabled,deployed FROM caesar_vless_devices WHERE client_id=?', (new_id,)).fetchall()
            self.assertEqual(rows, [('pc',1,0),('phone',1,0)])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_connect_links WHERE client_id=?', (new_id,)).fetchone()[0], 1)

    def test_full_mode_creates_real_client_without_pilot_marker(self):
        full = PanelOperations(self.db, allow_all=True, backup_dir=self.root / 'backups')
        result = full.execute('create', None, {'name':'Иван Иванов','period':'1m'}, 'full-client-create-20261119')
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT name FROM clients WHERE id=?',
                                         (result['id'],)).fetchone()[0], 'Иван Иванов')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_vless_devices WHERE client_id=?',
                                         (result['id'],)).fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_panel_test_created WHERE client_id=?',
                                         (result['id'],)).fetchone()[0], 0)
            self.assertIn('VPN MANAGER', con.execute('SELECT description FROM client_history WHERE client_id=?',
                                                     (result['id'],)).fetchone()[0])

    def test_full_mode_blocks_and_unblocks_both_keys_with_fast_sync_signal(self):
        signal = self.root / 'sync.signal'
        signal.touch()
        full = PanelOperations(self.db, allow_all=True, backup_dir=self.root / 'backups',
                               sync_signal_path=signal)
        full.execute('provision', 33, {}, 'provision-lifetime-user-20261119')
        blocked = full.execute('access', 33, {'enabled': False}, 'disable-both-devices-20261119')
        self.assertTrue(blocked['fast_sync_requested'])
        self.assertTrue(signal.read_text().strip())
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT SUM(manually_enabled) FROM caesar_vless_devices '
                                         'WHERE client_id=33').fetchone()[0], 0)
            self.assertIsNotNone(con.execute('SELECT token FROM caesar_connect_links WHERE client_id=33').fetchone())
        enabled = full.execute('access', 33, {'enabled': True}, 'enable-both-devices-20261119')
        self.assertTrue(enabled['fast_sync_requested'])
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT SUM(manually_enabled) FROM caesar_vless_devices '
                                         'WHERE client_id=33').fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM client_history WHERE client_id=33 '
                                         'AND event_type="client_access"').fetchone()[0], 2)

    def test_expired_subscription_cannot_be_unblocked(self):
        self.run_action('provision', 93)
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE clients SET end_date="2020-01-01" WHERE id=93')
        full = PanelOperations(self.db, allow_all=True, backup_dir=self.root / 'backups')
        full.execute('access', 93, {'enabled': False}, 'expire-block-both-20261119')
        with self.assertRaisesRegex(OperationError, 'extend'):
            full.execute('access', 93, {'enabled': True}, 'expire-enable-both-20261119')
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT SUM(manually_enabled) FROM caesar_vless_devices '
                                         'WHERE client_id=93').fetchone()[0], 0)

    def test_provision_toggle_archive_and_restore_with_uuid_retention(self):
        self.run_action('provision', 93)
        with sqlite3.connect(self.db) as con:
            before = con.execute('SELECT kind,uuid,token FROM caesar_vless_devices WHERE client_id=93 ORDER BY kind').fetchall()
            before_link = con.execute('SELECT token FROM caesar_connect_links WHERE client_id=93').fetchone()[0]
        self.run_action('toggle', 93, {'kind':'phone','enabled':False})
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT manually_enabled FROM caesar_vless_devices WHERE client_id=93 AND kind="phone"').fetchone()[0],0)
        self.run_action('archive', 93, {'confirm_name':'тест'})
        with sqlite3.connect(self.db) as con:
            after = con.execute('SELECT kind,uuid,token FROM caesar_vless_devices WHERE client_id=93 ORDER BY kind').fetchall()
            self.assertEqual([r[1] for r in after], [r[1] for r in before])
            self.assertNotEqual([r[2] for r in after], [r[2] for r in before])
            self.assertNotEqual(before_link, con.execute('SELECT token FROM caesar_connect_links WHERE client_id=93').fetchone()[0])
            self.assertEqual(con.execute('SELECT SUM(manually_enabled) FROM caesar_vless_devices WHERE client_id=93').fetchone()[0],0)
        with self.assertRaises(OperationError):
            self.run_action('extend',93,{'period':'1m'})
        self.run_action('restore',93)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_client_archives WHERE client_id=93').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT SUM(manually_enabled) FROM caesar_vless_devices WHERE client_id=93').fetchone()[0],0)

    def test_idempotent_extend_and_lifetime_protection(self):
        key = 'idempotent-extend-123456'
        first = self.run_action('extend', 93, {'period':'1m'}, request_id=key)
        before_replay_backups = len(list((self.root/'backups').glob('*.db')))
        second = self.run_action('extend', 93, {'period':'1m'}, request_id=key)
        self.assertEqual(first, second)
        self.assertEqual(len(list((self.root/'backups').glob('*.db'))),before_replay_backups)
        with self.assertRaises(OperationError) as err:
            self.run_action('extend', 93, {'period':'6m'}, request_id=key)
        self.assertEqual(err.exception.status,409)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM client_history WHERE client_id=93 AND event_type="extended"').fetchone()[0],1)
        self.run_action('extend',93,{'period':'lifetime'})
        with self.assertRaises(OperationError) as err:
            self.run_action('extend',93,{'period':'1m'})
        self.assertEqual(err.exception.status,409)

    def test_edit_and_backups(self):
        self.run_action('edit', 93, {'name':'тест обновлён','phone':'1234'})
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT name,phone FROM clients WHERE id=93').fetchone(),('тест обновлён','1234'))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM client_history WHERE client_id=93 AND event_type="edited"').fetchone()[0],1)
        self.assertGreaterEqual(len(list((self.root/'backups').glob('*.db'))),1)

    def test_month_end_adjustment(self):
        from datetime import date
        self.assertEqual(add_months(date(2026,1,31),1),date(2026,2,28))
        self.assertEqual(add_months(date(2024,1,31),1),date(2024,2,29))


if __name__ == '__main__':
    unittest.main()
