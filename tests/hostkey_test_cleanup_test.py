import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from server.hostkey_test_cleanup import CleanupError, apply, inventory
from server.xray_hostkey import init_tables


class TestCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / 'clients.db'
        self.xray = root / 'config.json'
        self.db_backup = root / 'db-backups'
        self.xray_backup = root / 'xray-backups'
        with sqlite3.connect(self.db) as con:
            con.executescript('''
                CREATE TABLE clients(id INTEGER PRIMARY KEY,user_id TEXT,name TEXT,referrer_id INTEGER);
                CREATE TABLE client_history(id INTEGER PRIMARY KEY,user_id TEXT,client_id INTEGER,
                    event_type TEXT,description TEXT,created_at TEXT);
                INSERT INTO clients VALUES(33,'u','Real user',NULL);
                INSERT INTO clients VALUES(88,'u','PROFILE LABEL TEST',NULL);
                INSERT INTO clients VALUES(92,'u','STAGING TEST',NULL);
                INSERT INTO clients VALUES(93,'u','тест',NULL);
            ''')
            init_tables(con)
            for kind, uuid in [('phone','uuid-test-phone'),('pc','uuid-test-pc')]:
                con.execute('INSERT INTO caesar_vless_devices(client_id,kind,uuid,token) VALUES(?,?,?,?)',
                            (93,kind,uuid,'secret-'+kind))
            for kind, uuid in [('phone','uuid-real-phone'),('pc','uuid-real-pc')]:
                con.execute('INSERT INTO caesar_vless_devices(client_id,kind,uuid,token) VALUES(?,?,?,?)',
                            (33,kind,uuid,'real-'+kind))
            con.execute('INSERT INTO caesar_connect_links(client_id,token) VALUES(93,"test-token")')
            con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES("u",93,"test","test")')
        self.config = {'inbounds':[{'tag':'vless-ws','protocol':'vless','listen':'127.0.0.1',
            'port':10000,'streamSettings':{'network':'ws','security':'none'},
            'settings':{'clients':[
                {'id':'uuid-test-phone','email':'test-phone'},
                {'id':'uuid-test-pc','email':'test-pc'},
                {'id':'uuid-real-phone','email':'real-phone'},
                {'id':'uuid-real-pc','email':'real-pc'},
                {'id':'uuid-unmanaged','email':'legacy-unknown'}]}}]}
        self.xray.write_text(json.dumps(self.config))
        self.ids = frozenset({88,92,93})

    def test_inventory_reports_only_known_test_keys_and_unknown_unmanaged(self):
        with sqlite3.connect(self.db) as con:
            result, uuids = inventory(con,self.config,self.ids)
        self.assertEqual(result['known_test_device_keys'],2)
        self.assertEqual(result['known_test_keys_still_in_xray'],2)
        self.assertEqual(len(result['unmanaged_xray_profiles_not_removed']),1)
        self.assertEqual(uuids,{'uuid-test-phone','uuid-test-pc'})

    def test_apply_removes_tests_only_after_live_xray_reconciliation(self):
        def fake_sync(db,xray_file,backup_dir,apply):
            with sqlite3.connect(db) as con:
                disabled={uuid for (uuid,) in con.execute(
                    'SELECT uuid FROM caesar_vless_devices WHERE manually_enabled=0')}
            config=json.loads(xray_file.read_text())
            config['inbounds'][0]['settings']['clients']=[c for c in config['inbounds'][0]['settings']['clients']
                                                        if c['id'] not in disabled]
            xray_file.write_text(json.dumps(config))
            return {'applied':True}
        with patch('server.hostkey_test_cleanup.sync',side_effect=fake_sync):
            result=apply(self.db,self.xray,self.ids,self.db_backup,self.xray_backup)
        self.assertEqual(result['remaining_clients'],1)
        self.assertTrue(Path(result['backup']).exists())
        self.assertEqual(result['unmanaged_xray_profiles_preserved'],1)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT id FROM clients').fetchall(),[(33,)])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_vless_devices').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_connect_links').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM client_history').fetchone()[0],0)
        after=json.loads(self.xray.read_text())['inbounds'][0]['settings']['clients']
        self.assertEqual({c['id'] for c in after}, {'uuid-real-phone','uuid-real-pc','uuid-unmanaged'})
        with sqlite3.connect(self.db) as con:
            self.assertTrue(inventory(con,json.loads(self.xray.read_text()),self.ids)[0]['already_removed'])

    def test_unexpected_referrer_blocks_deletion(self):
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE clients SET referrer_id=93 WHERE id=33')
            with self.assertRaisesRegex(CleanupError,'refer'):
                inventory(con,self.config,self.ids)


if __name__ == '__main__':
    unittest.main()
