from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import unittest
import awg_provision_test as base
from awg_persistence import StateError
from awg_records import Records


class RecordsTests(unittest.TestCase):
    def setUp(self):
        base.ProvisionTests.setUp(self)
        self.store = Records(self.db, self.config, self.profiles, self.root/'state', self.runtime, protected_ids=())
        self.store.owner_id = 'owner'
        self.clock = datetime(2026,11,13,tzinfo=timezone.utc)
    def tearDown(self): base.ProvisionTests.tearDown(self)
    def orphan(self):
        folder = self.profiles/'61'; folder.mkdir()
        public = base.key('imported-61')
        (folder/'metadata.json').write_text(json.dumps(dict(client_id=61, name='Imported', public_key=public, address='10.99.0.61', period='lifetime', end_date=None)))
        (folder/'client.conf').write_text('Original private profile; must not be modified')
        self.config.write_text(self.config.read_text()+'[Peer]\nPublicKey = '+public+'\nAllowedIPs = 10.99.0.61/32\n')
        self.runtime.keys.add(public)
    def snapshot(self):
        return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [self.config,*self.profiles.rglob('*')] if p.is_file()}
    def test_import_links_existing_profile_once_without_key_generation_or_file_changes(self):
        self.orphan(); before=self.snapshot(); live=set(self.runtime.keys)
        self.assertEqual([c['id'] for c in self.store.candidates()], [61])
        result=self.store.import_profiles([61], 'import-existing-0001', self.clock)
        self.assertEqual(self.store.import_profiles([61], 'import-existing-0001', self.clock),result)
        self.assertEqual(self.store.candidates(),[])
        self.assertEqual(self.snapshot(),before); self.assertEqual(self.runtime.keys,live)
        self.assertEqual(self.runtime.generated,0)
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM clients').fetchone()[0],61)
            self.assertIsNone(con.execute('SELECT end_date FROM clients WHERE id=61').fetchone()[0])
            self.assertEqual(con.execute("SELECT count(*) FROM client_history WHERE event_type='imported'").fetchone()[0],1)
    def test_import_lost_success_response_replays_without_duplicate_history(self):
        self.orphan()
        self.store.fault=lambda p:(_ for _ in ()).throw(RuntimeError('lost reply')) if p=='db_committed' else None
        with self.assertRaises(RuntimeError): self.store.import_profiles([61],'import-lost-reply01',self.clock)
        self.store.fault=lambda p:None
        self.assertEqual(self.store.import_profiles([61],'import-lost-reply01',self.clock)['imported'],[61])
    def test_import_failure_rolls_back_database_and_leaves_original_material(self):
        self.orphan(); before=self.snapshot()
        self.store.fault=lambda p:(_ for _ in ()).throw(RuntimeError('crash')) if p=='before_db_commit' else None
        with self.assertRaises(RuntimeError): self.store.import_profiles([61],'import-crash-000001',self.clock)
        with self.store.connect() as con: self.assertEqual(con.execute('SELECT count(*) FROM clients').fetchone()[0],60)
        self.assertEqual(self.snapshot(),before)
    def test_import_refuses_runtime_drift_and_duplicate_address(self):
        self.orphan(); self.runtime.available=False
        with self.assertRaises(StateError): self.store.import_profiles([61],'import-offline-0001',self.clock)
        self.runtime.available=True
        path=self.profiles/'61/metadata.json'; data=json.loads(path.read_text()); data['address']='10.99.0.1'; path.write_text(json.dumps(data))
        with self.assertRaises(StateError): self.store.candidates()
    def test_delivery_is_a_manual_record_not_a_handshake_or_vpn_mutation(self):
        before=self.snapshot(); self.runtime.available=False
        result=self.store.delivery(1,True,'delivery-marker-001',self.clock)
        self.assertEqual(result['delivered_at'],self.clock.isoformat())
        self.assertEqual(self.store.delivery(1,True,'delivery-marker-001',self.clock),result)
        self.store.delivery(1,False,'delivery-reset-0001',self.clock)
        with self.store.connect() as con:
            self.assertIsNone(con.execute('SELECT delivered_at FROM profile_delivery WHERE client_id=1').fetchone()[0])
            self.assertEqual(con.execute('SELECT count(*) FROM client_history WHERE client_id=1').fetchone()[0],2)
        self.assertEqual(self.snapshot(),before); self.assertEqual(self.runtime.calls,[])
    def test_delivery_rejects_invalid_status_archived_client_and_key_reuse(self):
        with self.assertRaises(StateError): self.store.delivery(1,'yes','delivery-invalid01',self.clock)
        self.store.delivery(1,True,'delivery-reuse-0001',self.clock)
        with self.assertRaises(StateError): self.store.delivery(2,True,'delivery-reuse-0001',self.clock)
        with self.store.connect() as con:
            con.execute('CREATE TABLE awg_deleted_clients(client_id INTEGER PRIMARY KEY)')
            con.execute('INSERT INTO awg_deleted_clients VALUES(2)')
        with self.assertRaises(StateError): self.store.delivery(2,True,'delivery-archived01',self.clock)
