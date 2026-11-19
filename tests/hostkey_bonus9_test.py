import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
import unittest

from server.hostkey_bonus9 import CAMPAIGN, CompensationError, calculation, compensate
from server.hostkey_bonus14 import CAMPAIGN as PRIOR_CAMPAIGN
from server.xray_hostkey import MOSCOW


class AdditionalNineDaysTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / 'clients.db'
        self.backups = root / 'backups'
        self.now = datetime(2026, 11, 19, 15, 0, tzinfo=MOSCOW)
        self.test_ids = frozenset({4})
        with sqlite3.connect(self.db) as con:
            con.executescript('''
                CREATE TABLE clients(id INTEGER PRIMARY KEY,user_id TEXT,name TEXT,period TEXT,end_date TEXT);
                CREATE TABLE client_history(id INTEGER PRIMARY KEY,user_id TEXT,client_id INTEGER,
                    event_type TEXT,description TEXT,created_at TEXT);
                CREATE TABLE caesar_billing_campaigns(campaign_id TEXT PRIMARY KEY,days INTEGER,
                    affected_clients INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO clients VALUES(1,'u','Active subscriber','6m','2027-05-27');
                INSERT INTO clients VALUES(2,'u','Now expired','1m','2026-11-18');
                INSERT INTO clients VALUES(3,'u','Changed to lifetime','lifetime',NULL);
                INSERT INTO clients VALUES(4,'u','TEST account','1m','2027-05-27');
                INSERT INTO clients VALUES(5,'u','New after migration','1m','2027-05-27');
            ''')
            con.execute('INSERT INTO caesar_billing_campaigns(campaign_id,days,affected_clients) VALUES(?,?,?)',
                        (PRIOR_CAMPAIGN, 14, 3))
            for cid in (1, 2, 3):
                con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',
                            ('u', cid, 'compensation_14d', PRIOR_CAMPAIGN + ': migrated (+14 calendar days)'))

    def test_plan_reuses_original_cohort_and_excludes_currently_inactive(self):
        with sqlite3.connect(self.db) as con:
            plan, updates = calculation(con, self.test_ids, self.now)
        self.assertEqual(plan['prior_bonus_clients'], 3)
        self.assertEqual(plan['eligible_clients'], 1)
        self.assertEqual(plan['excluded_expired'], [2])
        self.assertEqual(plan['excluded_lifetime'], [3])
        self.assertEqual(updates[0][4], '2027-06-05')

    def test_apply_once_with_history_and_backup(self):
        result = compensate(self.db, self.test_ids, self.backups, self.now)
        self.assertEqual(result['changed'], 1)
        self.assertTrue(Path(result['backup']).is_file())
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=1').fetchone()[0], '2027-06-05')
            self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=5').fetchone()[0], '2027-05-27')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM client_history WHERE event_type="compensation_9d"').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT days FROM caesar_billing_campaigns WHERE campaign_id=?',
                                         (CAMPAIGN,)).fetchone()[0], 9)
        self.assertEqual(compensate(self.db, self.test_ids, self.backups, self.now)['changed'], 0)

    def test_missing_original_campaign_is_blocked(self):
        with sqlite3.connect(self.db) as con:
            con.execute('DELETE FROM caesar_billing_campaigns')
        with self.assertRaisesRegex(CompensationError, r'Original \+14-day campaign'):
            compensate(self.db, self.test_ids, self.backups, self.now)
        self.assertFalse(self.backups.exists())

    def test_mismatched_original_history_is_blocked(self):
        with sqlite3.connect(self.db) as con:
            con.execute('DELETE FROM client_history WHERE client_id=3')
        with self.assertRaisesRegex(CompensationError, 'history does not match'):
            compensate(self.db, self.test_ids, self.backups, self.now)

    def test_unknown_end_date_prevents_partial_award(self):
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE clients SET end_date=NULL WHERE id=1')
        with self.assertRaisesRegex(CompensationError, 'Unverified dates'):
            compensate(self.db, self.test_ids, self.backups, self.now)
        self.assertFalse(self.backups.exists())

    def test_previously_deleted_test_ids_are_safe_to_exclude(self):
        with sqlite3.connect(self.db) as con:
            con.execute('DELETE FROM clients WHERE id=4')
            plan, updates = calculation(con, self.test_ids, self.now)
        self.assertEqual(plan['eligible_clients'], 1)
        self.assertEqual(updates[0][0], 1)


if __name__ == '__main__':
    unittest.main()
