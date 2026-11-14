import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from awg_persistence import PeerStore, StateError, peer_blocks


class Runtime:
    def __init__(self, keys):
        self.keys = set(keys)
        self.available = True
        self.calls = []
    def peers(self):
        if not self.available:
            raise StateError('unavailable')
        return set(self.keys)
    def is_down(self):
        return not self.available
    def apply(self, public, block):
        self.peers()
        self.calls.append((public, block is not None))
        if block is None:
            self.keys.discard(public)
        else:
            self.keys.add(public)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'clients.db'
        self.config = self.root / 'awg.conf'
        self.profiles = self.root / 'profiles'
        self.keys = {i: base64.b64encode(hashlib.sha256(str(i).encode()).digest()).decode() for i in range(1, 61)}
        with sqlite3.connect(self.db) as con:
            con.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE clients(id INTEGER PRIMARY KEY,user_id TEXT,name TEXT);
                CREATE TABLE client_history(id INTEGER PRIMARY KEY,user_id TEXT,client_id INTEGER,event_type TEXT,description TEXT);''')
            for i in range(1, 61):
                con.execute('INSERT INTO clients VALUES(?,?,?)',(i,'owner','Synthetic'))
                folder = self.profiles / str(i)
                folder.mkdir(parents=True)
                (folder/'metadata.json').write_text(json.dumps(dict(client_id=i,public_key=self.keys[i],address='10.99.0.'+str(i))))
        self.original = '[Interface]\nPrivateKey = unchanged\nListenPort = 51840\n\n' + ''.join(
            '[Peer]\nPublicKey = '+self.keys[i]+'\nAllowedIPs = 10.99.0.'+str(i)+'/32\n\n' for i in range(1,61))
        self.config.write_text(self.original)
        self.runtime = Runtime([*self.keys.values(), 'unmanaged-test-peer'])
        self.store = PeerStore(self.db,self.config,self.profiles,self.root/'state',self.runtime,protected_ids=range(1,60))
    def tearDown(self):
        self.temp.cleanup()
    def records(self):
        with sqlite3.connect(self.db) as con:
            return con.execute('SELECT count(*) FROM client_history').fetchone()[0]
    def assert_existing_preserved(self):
        _, blocks = peer_blocks(self.config.read_text())
        fields = {key:value for key,value,_ in blocks}
        for i in range(1,60):
            self.assertEqual(fields[self.keys[i]]['AllowedIPs'],'10.99.0.'+str(i)+'/32')
            self.assertIn(self.keys[i],self.runtime.keys)
        self.assertIn('unmanaged-test-peer',self.runtime.keys)
    def test_toggle_roundtrip_preserves_all_59_and_unmanaged_peer(self):
        self.store.toggle(60,False,'disable-test-0001')
        self.assertNotIn(self.keys[60],self.runtime.keys)
        self.assert_existing_preserved()
        # Fresh manager reads the persisted disabled block.
        fresh=PeerStore(self.db,self.config,self.profiles,self.root/'state',self.runtime,protected_ids=range(1,60))
        fresh.toggle(60,True,'enable-test-00001')
        self.assertIn(self.keys[60],self.runtime.keys)
        self.assertEqual(self.records(),2)
        self.assert_existing_preserved()
    def test_existing_clients_require_explicit_authorization(self):
        with self.assertRaises(StateError):
            self.store.toggle(1,False,'protected-test-01')
        self.assertEqual(self.config.read_text(),self.original)
        self.assertEqual(self.runtime.calls,[])
    def test_retry_after_lost_response_is_idempotent(self):
        def fail(point):
            if point=='db_committed': raise RuntimeError('response lost')
        self.store.fault=fail
        with self.assertRaises(RuntimeError): self.store.toggle(60,False,'lost-response-001')
        self.store.fault=lambda p:None
        result=self.store.toggle(60,False,'lost-response-001')
        self.assertFalse(result['enabled'])
        self.assertEqual(self.records(),1)
        with self.assertRaises(StateError): self.store.toggle(60,True,'lost-response-001')
    def test_failures_before_commit_recover_previous_state(self):
        for point in ['prepared','config_written','metadata_written','runtime_applied']:
            with self.subTest(point=point):
                self.store.fault=lambda p: (_ for _ in ()).throw(RuntimeError('crash')) if p==point else None
                with self.assertRaises(RuntimeError): self.store.toggle(60,False,'crash-'+point+'-0001')
                self.store.fault=lambda p:None
                self.assertEqual(self.store.recover(),'before')
                self.assertEqual(self.config.read_text(),self.original)
                self.assertIn(self.keys[60],self.runtime.keys)
                self.assertEqual(self.records(),0)
                self.assert_existing_preserved()
    def test_awg_unavailable_does_not_change_disk_or_database(self):
        self.runtime.available=False
        with self.assertRaises(StateError): self.store.toggle(60,False,'unavailable-0001')
        self.assertEqual(self.config.read_text(),self.original)
        self.assertEqual(self.records(),0)
    def test_drift_and_invalid_id_fail_closed(self):
        self.runtime.keys.remove(self.keys[60])
        with self.assertRaises(StateError): self.store.toggle(60,False,'drift-detected-01')
        with self.assertRaises(StateError): self.store.toggle('../60',True,'invalid-target-01')
        self.assertEqual(self.config.read_text(),self.original)
    def test_boot_recovery_restores_disk_without_starting_interface(self):
        def fail(point):
            if point=='config_written': raise RuntimeError('power lost')
        self.store.fault=fail
        with self.assertRaises(RuntimeError): self.store.toggle(60,False,'power-loss-00001')
        with self.assertRaises(StateError): self.store.recover(offline=True)
        self.runtime.available=False
        self.assertEqual(self.store.recover(offline=True),'before')
        self.assertEqual(self.config.read_text(),self.original)
        self.assertEqual(self.runtime.calls,[])
    def test_backups_and_journals_are_private(self):
        self.store.toggle(60,False,'permissions-0001')
        for path in (self.root/'state').rglob('*'):
            if path.name!='lock': self.assertEqual(path.stat().st_mode & 0o077,0)
        backups=list((self.root/'state').glob('op-*/clients.db'))
        self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(con.execute('SELECT count(*) FROM clients').fetchone()[0],60)


if __name__=='__main__': unittest.main()
