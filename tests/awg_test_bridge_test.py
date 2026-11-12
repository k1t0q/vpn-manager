import json
import unittest
import awg_provision_test as base
from awg_persistence import StateError,peer_blocks
from awg_test_bridge import TestBridge


class TestBoundaryTests(unittest.TestCase):
 def setUp(self):
  base.ProvisionTests.setUp(self)
  self.guard=TestBridge(self.store,self.template,dict(peers={key:fields for key,fields,_ in peer_blocks(self.config.read_text())[1]},runtime_peers=list(self.runtime.keys)))
 def tearDown(self):base.ProvisionTests.tearDown(self)
 def test_existing_peer_and_referrer_operations_are_always_denied(self):
  for action in ('enable','disable','delete','config','qr'):
   with self.subTest(action=action):
    with self.assertRaises(StateError):self.guard.dispatch('GET' if action in ('config','qr') else 'POST','/api/wg/client/1/'+action,{},'protected-action-01')
  with self.assertRaises(StateError):self.guard.dispatch('POST','/api/managed/create',{**self.data,'name':'STAGING TEST Referrer','referrer_id':1},'protected-ref-0001')
  base.ProvisionTests.assert_originals(self)
 def test_new_test_profile_roundtrip_and_archive(self):
  data={**self.data,'name':'STAGING TEST Lifecycle'}
  result,status,_=self.guard.dispatch('POST','/api/managed/create',data,'test-bridge-create-01')
  self.assertEqual(status,201);self.assertEqual(result['id'],'61')
  self.assertEqual(self.guard.dispatch('POST','/api/managed/create',data,'test-bridge-create-01')[0],result)
  self.guard.dispatch('POST','/api/wg/client/61/disable',{},'test-bridge-disable-1')
  self.guard.dispatch('POST','/api/wg/client/61/enable',{},'test-bridge-enable-01')
  self.assertTrue(self.guard.dispatch('POST','/api/wg/client/61/delete',{},'test-bridge-delete-01')[0]['history_preserved'])
  base.ProvisionTests.assert_originals(self)
 def test_baseline_drift_denies_new_creation(self):
  self.config.write_text(self.original.replace('10.99.0.1/32','10.98.0.1/32'))
  with self.assertRaises(StateError):self.guard.dispatch('POST','/api/managed/create',{**self.data,'name':'STAGING TEST Drift'},'test-drift-create-1')
  self.assertEqual(self.runtime.generated,0)
 def test_limit_is_enforced_inside_engine_transaction(self):
  self.store.stage_limit=1
  data={**self.data,'name':'STAGING TEST Limit'}
  result=self.guard.dispatch('POST','/api/managed/create',data,'test-limited-create1')[0]
  with self.assertRaises(StateError):self.guard.dispatch('POST','/api/managed/create',data,'test-limited-create2')
  self.assertEqual(self.guard.dispatch('POST','/api/managed/create',data,'test-limited-create1')[0],result)


if __name__=='__main__':unittest.main()
