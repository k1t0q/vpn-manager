import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
import unittest

from server.hostkey_bonus14 import CompensationError, calculation, compensate
from server.xray_hostkey import MOSCOW


class Bonus14Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / 'clients.db'
        self.backups = root / 'backups'
        self.now = datetime(2026, 11, 19, 15, 0, tzinfo=MOSCOW)
        with sqlite3.connect(self.db) as con:
            con.executescript('''
                CREATE TABLE clients(id INTEGER PRIMARY KEY,user_id TEXT,name TEXT,period TEXT,end_date TEXT);
                CREATE TABLE client_history(id INTEGER PRIMARY KEY,user_id TEXT,client_id INTEGER,
                    event_type TEXT,description TEXT,created_at TEXT);
                INSERT INTO clients VALUES(1,'u','Active one month','1m','2026-12-14');
                INSERT INTO clients VALUES(2,'u','Expired six months','6m','2026-11-18');
                INSERT INTO clients VALUES(3,'u','Lifetime','lifetime',NULL);
                INSERT INTO clients VALUES(4,'u','Ends today','12m','2026-11-19');
                INSERT INTO clients VALUES(5,'u','TEST keep','1m','2026-12-14');
                INSERT INTO clients VALUES(6,'u','Active time','1m','2026-12-10T23:00:00+03:00');
                INSERT INTO clients VALUES(7,'u','Trial ended earlier today','trial','2026-11-19T13:00:00+03:00');
                INSERT INTO clients VALUES(8,'u','Trial still active','trial','2026-11-19T16:00:00+03:00');
            ''')
        self.excluded = frozenset({5})

    def test_plan_selects_only_active_not_lifetime_or_test(self):
        with sqlite3.connect(self.db) as con:
            plan, updates = calculation(con, self.excluded, self.now)
        self.assertEqual([row[0] for row in updates], [1,4,6,8])
        self.assertEqual(plan['expired_ids_excluded'], [2,7])
        self.assertEqual(plan['lifetime_ids_excluded'], [3])
        self.assertEqual(updates[0][4], '2026-12-28')
        self.assertEqual(updates[1][4], '2026-12-03')
        self.assertEqual(updates[2][4], '2026-12-24T23:00:00+03:00')

    def test_apply_audits_and_prevents_double_grant(self):
        result = compensate(self.db, self.excluded, self.backups, self.now)
        self.assertEqual(result['changed'], 4)
        self.assertTrue(Path(result['backup']).exists())
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=1').fetchone()[0], '2026-12-28')
            self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=2').fetchone()[0], '2026-11-18')
            self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=3').fetchone()[0], None)
            self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=5').fetchone()[0], '2026-12-14')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM client_history WHERE event_type="compensation_14d"').fetchone()[0], 4)
        again = compensate(self.db, self.excluded, self.backups, self.now)
        self.assertTrue(again['already_applied'])
        self.assertEqual(again['changed'], 0)

    def test_invalid_end_date_prevents_all_changes(self):
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE clients SET end_date=NULL WHERE id=1')
        with self.assertRaisesRegex(CompensationError, 'Unverified end dates'):
            compensate(self.db, self.excluded, self.backups, self.now)
        self.assertFalse(self.backups.exists())

    def test_unexpected_test_ids_abort(self):
        with sqlite3.connect(self.db) as con:
            with self.assertRaisesRegex(CompensationError, 'Test exclusions mismatch'):
                calculation(con, frozenset(), self.now)


if __name__ == '__main__':
    unittest.main()
