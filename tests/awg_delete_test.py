import json
import sqlite3
import unittest
import awg_persistence_test as base
from awg_delete import Deleter
from awg_persistence import StateError


class DeleteTests(unittest.TestCase):
 def setUp(self):
  base.PersistenceTests.setUp(self)
  self.profile=self.profiles/'60'
  (self.profile/'client.conf').write_text('original-test-private-material')
  with sqlite3.connect(self.db) as con:
   con.execute("INSERT INTO client_history(user_id,client_id,event_type,description) VALUES('owner',60,'created','Keep original history')")
  self.store=Deleter(self.db,self.config,self.profiles,self.root/'state',self.runtime,protected_ids=range(1,60))
 def tearDown(self):base.PersistenceTests.tearDown(self)
 def assert_preserved(self):base.PersistenceTests.assert_existing_preserved(self)
 def test_delete_archives_profile_and_keeps_history_and_retries(self):
  result=self.store.delete(60,'delete-test-00001')
  self.assertTrue(result['history_preserved']);self.assertFalse(self.profile.exists())
  self.assertNotIn(self.keys[60],self.runtime.keys)
  self.assertEqual(self.store.delete(60,'delete-test-00001'),result)
  with sqlite3.connect(self.db) as con:
   self.assertEqual(con.execute('SELECT count(*) FROM clients').fetchone()[0],60)
   self.assertEqual(con.execute('SELECT count(*) FROM client_history WHERE client_id=60').fetchone()[0],2)
   self.assertEqual(con.execute('SELECT client_id FROM awg_deleted_clients').fetchone()[0],60)
  self.assertTrue(list((self.root/'state').glob('op-*/removed-profile/client.conf')))
  self.assert_preserved()
 def test_crash_at_each_precommit_phase_restores_profile_and_history(self):
  for point in ['prepared','config_written','runtime_applied','profile_archived']:
   with self.subTest(point=point):
    self.store.fault=lambda p:(_ for _ in ()).throw(RuntimeError('crash')) if p==point else None
    with self.assertRaises(RuntimeError):self.store.delete(60,'delete-fault-'+point)
    self.store.fault=lambda _:None
    self.assertEqual(self.store.recover(),'before')
    self.assertEqual((self.profile/'client.conf').read_text(),'original-test-private-material')
    self.assertEqual(self.config.read_text(),self.original)
    self.assertIn(self.keys[60],self.runtime.keys)
    with sqlite3.connect(self.db) as con:self.assertEqual(con.execute('SELECT count(*) FROM client_history').fetchone()[0],1)
    self.assert_preserved()
 def test_lost_committed_response_keeps_profile_deleted(self):
  self.store.fault=lambda p:(_ for _ in ()).throw(RuntimeError('lost reply')) if p=='db_committed' else None
  with self.assertRaises(RuntimeError):self.store.delete(60,'delete-lost-00001')
  self.store.fault=lambda _:None
  self.assertTrue(self.store.delete(60,'delete-lost-00001')['deleted'])
  self.assertFalse(self.profile.exists());self.assert_preserved()
 def test_protected_and_unavailable_fail_without_changes(self):
  with self.assertRaises(StateError):self.store.delete(1,'delete-protected-01')
  self.runtime.available=False
  with self.assertRaises(StateError):self.store.delete(60,'delete-offline-0001')
  self.assertTrue(self.profile.exists());self.assertEqual(self.config.read_text(),self.original)


if __name__=='__main__':unittest.main()
