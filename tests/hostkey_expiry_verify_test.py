import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
import importlib.util

source = Path(__file__).resolve().parents[1] / 'deploy/hostkey-expiry-verify.py'
spec = importlib.util.spec_from_file_location('hostkey_expiry_verify', source)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class ExpiryAuditTests(unittest.TestCase):
    def test_expired_managed_uuid_in_live_xray_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            db = path / 'db.sqlite'
            xray = path / 'config.json'
            with sqlite3.connect(db) as con:
                con.execute('CREATE TABLE clients(id INTEGER PRIMARY KEY,period TEXT,end_date TEXT)')
                con.execute("INSERT INTO clients VALUES(1,'1m','2020-01-01')")
                con.execute('CREATE TABLE caesar_vless_devices(client_id INTEGER,kind TEXT,uuid TEXT,token TEXT,manually_enabled INTEGER,deployed INTEGER)')
                con.execute("INSERT INTO caesar_vless_devices VALUES(1,'phone','expired-key','token',1,1)")
            config = {'inbounds': [{'tag': 'vless-ws', 'protocol':'vless', 'listen':'127.0.0.1',
                'port':10000,'settings':{'clients':[{'id':'unmanaged-test'}, {'id':'expired-key'}]},
                'streamSettings':{'network':'ws'}}]}
            xray.write_text(json.dumps(config))
            failed = verify.inspect(db, xray)
            self.assertFalse(failed['ok'])
            self.assertEqual(failed['expired_keys_still_active'], 1)
            config['inbounds'][0]['settings']['clients'].pop()
            xray.write_text(json.dumps(config))
            self.assertTrue(verify.inspect(db, xray)['ok'])


if __name__ == '__main__':
    unittest.main()
