import sqlite3
import tempfile
from pathlib import Path
import unittest

from server.hostkey_bulk_migrate import BulkMigrationError, apply, parse_ids, plan


class HostkeyBulkMigrateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / 'clients.db'
        self.backups = root / 'backups'
        with sqlite3.connect(self.db) as con:
            con.executescript('''
                CREATE TABLE clients(id INTEGER PRIMARY KEY,user_id TEXT,name TEXT,period TEXT,end_date TEXT);
                CREATE TABLE client_history(id INTEGER PRIMARY KEY,user_id TEXT,client_id INTEGER,
                    event_type TEXT,description TEXT,created_at TEXT);
                INSERT INTO clients VALUES(1,'u','Real active','1m','2999-12-30');
                INSERT INTO clients VALUES(2,'u','Real expired','1m','2020-01-01');
                INSERT INTO clients VALUES(3,'u','TEST temporary','lifetime',NULL);
                INSERT INTO clients VALUES(4,'u','ТЕСТ temporary','1m','2999-12-30');
                INSERT INTO clients VALUES(5,'u','Real lifetime','lifetime',NULL);
            ''')
        self.excluded = frozenset({3, 4})

    def test_plan_counts_paid_and_inactive_but_never_test(self):
        with sqlite3.connect(self.db) as con:
            result = plan(con, self.excluded)
        self.assertEqual(result['total_clients'], 5)
        self.assertEqual(result['test_ids_excluded'], [3,4])
        self.assertEqual(result['clients_to_register'], 3)
        self.assertEqual(result['active_to_register'], 2)
        self.assertEqual(result['inactive_to_register'], 1)
        self.assertEqual(result['pending_ids'], [1,2,5])

    def test_registration_is_audited_backed_up_and_idempotent(self):
        result = apply(self.db, self.excluded, 3, 2, self.backups)
        self.assertTrue(result['ok'])
        self.assertTrue(Path(result['backup']).exists())
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_vless_devices').fetchone()[0], 6)
            self.assertEqual(con.execute('SELECT COUNT(DISTINCT token) FROM caesar_vless_devices').fetchone()[0], 6)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_connect_links').fetchone()[0], 3)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM client_history').fetchone()[0], 3)
            self.assertEqual(plan(con, self.excluded)['clients_to_register'], 0)
        with self.assertRaisesRegex(BulkMigrationError, 'Confirmation counts changed'):
            apply(self.db, self.excluded, 3, 2, self.backups)

    def test_missing_test_exclusion_blocks_everything(self):
        with sqlite3.connect(self.db) as con:
            with self.assertRaisesRegex(BulkMigrationError, 'missing='):
                plan(con, frozenset({3}))
        self.assertEqual(parse_ids('3,4'), self.excluded)
        with self.assertRaises(BulkMigrationError):
            parse_ids('3,3')

    def test_bad_confirmation_does_not_create_backup_or_profiles(self):
        with self.assertRaisesRegex(BulkMigrationError, 'Confirmation counts changed'):
            apply(self.db, self.excluded, 4, 2, self.backups)
        self.assertFalse(self.backups.exists())
        with sqlite3.connect(self.db) as con:
            self.assertFalse(con.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_vless_devices'").fetchone())


if __name__ == '__main__':
    unittest.main()
