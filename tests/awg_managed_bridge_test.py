from datetime import datetime, timezone
import json
import sqlite3
import unittest
import awg_provision_test as base
from awg_persistence import StateError, peer_blocks
from awg_managed_bridge import ManagedBridge
from awg_provision import Provisioner


class ManagedBridgeTests(unittest.TestCase):
    def setUp(self):
        base.ProvisionTests.setUp(self)
        self.store = Provisioner(self.db, self.config, self.profiles, self.root/'state', self.runtime, protected_ids=())
        self.store.owner_id = 'owner'
        _, blocks = peer_blocks(self.config.read_text())
        fields = {key: value for key, value, _ in blocks}
        clients = {}
        for cid, public in self.keys.items():
            clients[str(cid)] = dict(public_key=public, address='10.99.0.'+str(cid), peer_fields=fields[public])
            with sqlite3.connect(self.db) as con:
                con.execute('UPDATE clients SET wg_client_id=? WHERE id=?', ('legacy-'+str(cid), cid))
        self.baseline = dict(clients=clients, runtime_peers=list(self.runtime.keys), initial_expiry_hold={})
        self.bridge = ManagedBridge(self.store, self.template, self.baseline)
    def tearDown(self):
        base.ProvisionTests.tearDown(self)
    def dispatch(self, path, data=None, key='managed-operation-0001'):
        return self.bridge.dispatch('POST', path, data, key)[0]
    def test_original_legacy_id_toggle_roundtrip_preserves_keys_and_addresses(self):
        before = {p: p.read_bytes() for p in self.profiles.rglob('*') if p.is_file()}
        self.assertFalse(self.dispatch('/api/wg/client/legacy-1/disable')['enabled'])
        self.bridge.guard()
        self.assertTrue(self.dispatch('/api/wg/client/legacy-1/enable', key='managed-enable-0001')['enabled'])
        self.bridge.guard()
        for path, content in before.items():
            if path.parent.name != '1':
                self.assertEqual(path.read_bytes(), content)
        self.assertEqual(json.loads((self.profiles/'1/metadata.json').read_text())['public_key'], self.keys[1])
        self.assertIn('unmanaged-test-peer', self.runtime.keys)
    def test_initial_expired_hold_survives_tick_and_name_edit_then_releases_on_renewal(self):
        self.baseline['initial_expiry_hold'] = {'1': '2026-01-01'}
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE clients SET end_date='2026-01-01' WHERE id=1")
        meta = json.loads((self.profiles/'1/metadata.json').read_text())
        meta.update(period='1m', end_date='2026-01-01')
        (self.profiles/'1/metadata.json').write_text(json.dumps(meta))
        clock = datetime(2026, 11, 13, tzinfo=timezone.utc)
        self.bridge.tick(clock)
        self.assertIn(self.keys[1], self.runtime.keys)
        result = self.dispatch('/api/managed/clients/1/edit', {'name': 'Renamed'})
        self.assertTrue(result['enabled'])
        self.dispatch('/api/managed/clients/1/extend', {'period': '1m'}, 'renew-original-0001')
        with self.store.connect() as con:
            end = con.execute('SELECT end_date FROM clients WHERE id=1').fetchone()[0]
        expiry = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        self.bridge.tick(expiry)
        self.assertNotIn(self.keys[1], self.runtime.keys)
    def test_guard_rejects_key_ip_peer_field_or_unmanaged_runtime_drift(self):
        self.runtime.keys.remove('unmanaged-test-peer')
        with self.assertRaises(StateError): self.bridge.guard()
        self.runtime.keys.add('unmanaged-test-peer')
        self.config.write_text(self.config.read_text().replace('10.99.0.1/32', '10.99.0.200/32'))
        with self.assertRaises(StateError): self.bridge.guard()
    def test_canonical_delete_preserves_original_row_and_history(self):
        result = self.dispatch('/api/managed/clients/1/delete')
        self.assertTrue(result['history_preserved'])
        self.bridge.guard()
        with self.store.connect() as con:
            self.assertIsNotNone(con.execute('SELECT id FROM clients WHERE id=1').fetchone())
            self.assertEqual(con.execute("SELECT count(*) FROM client_history WHERE client_id=1 AND event_type='deleted'").fetchone()[0], 1)
        self.assertNotIn(self.keys[1], self.runtime.keys)
    def test_ambiguous_numeric_legacy_mapping_fails_before_mutation(self):
        with sqlite3.connect(self.db) as con: con.execute("UPDATE clients SET wg_client_id='1' WHERE id=2")
        before = self.config.read_bytes()
        with self.assertRaises(StateError): self.dispatch('/api/wg/client/1/disable')
        self.assertEqual(self.config.read_bytes(), before)
    def test_production_creation_does_not_require_test_prefix(self):
        result = self.dispatch('/api/managed/create', self.data)
        self.assertEqual(result['id'], '61')
        self.bridge.guard()
    def test_legacy_owner_referrer_bonus_preserves_both_owners(self):
        with sqlite3.connect(self.db) as con: con.execute("UPDATE clients SET user_id='legacy-owner' WHERE id=2")
        result=self.dispatch('/api/managed/create',{**self.data,'referrer_id':2})
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT user_id FROM clients WHERE id=2').fetchone()[0],'legacy-owner')
            self.assertEqual(con.execute('SELECT user_id FROM clients WHERE id=?',(result['id'],)).fetchone()[0],'owner')
            self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=2').fetchone()[0],'2027-01-15')
        self.bridge.guard()
    def test_legacy_numeric_tariff_allows_name_edit_without_changing_period(self):
        with sqlite3.connect(self.db) as con: con.execute("UPDATE clients SET period='1' WHERE id=1")
        self.dispatch('/api/managed/clients/1/edit',{'name':'Legacy name'})
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT period FROM clients WHERE id=1').fetchone()[0],'1')
    def test_referred_trial_keeps_its_exact_deadline_when_bonus_is_added(self):
        stamp='2027-01-01T22:30:00+00:00'
        with sqlite3.connect(self.db) as con: con.execute("UPDATE clients SET period='trial',end_date=? WHERE id=2",(stamp,))
        self.dispatch('/api/managed/create',{**self.data,'referrer_id':2})
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=2').fetchone()[0],'2027-01-15T22:30:00+00:00')
    def test_expiry_hold_cannot_return_after_explicit_renewal(self):
        self.baseline['initial_expiry_hold']={'1':'2026-01-01'}
        with sqlite3.connect(self.db) as con: con.execute("UPDATE clients SET end_date='2026-01-01' WHERE id=1")
        self.dispatch('/api/managed/clients/1/extend',{'period':'1m'},'release-hold-renew1')
        result=self.dispatch('/api/managed/clients/1/edit',{'end_date':'2026-01-01'},'release-hold-reset1')
        self.assertFalse(result['enabled'])
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM initial_expiry_hold_releases WHERE client_id=1').fetchone()[0],1)
    def test_imported_preexisting_peer_can_be_disabled_without_guard_false_alarm(self):
        import awg_records_test
        awg_records_test.RecordsTests.orphan(self)
        public=base.key('imported-61')
        self.baseline['runtime_peers'].append(public)
        self.baseline['unmanaged_disk_peers']={public:dict(PublicKey=public,AllowedIPs='10.99.0.61/32')}
        result=self.dispatch('/api/managed/import',{'client_ids':[61]})
        self.assertEqual(result['imported'],[61])
        self.dispatch('/api/wg/client/61/disable',key='disable-imported01')
        self.bridge.guard()
