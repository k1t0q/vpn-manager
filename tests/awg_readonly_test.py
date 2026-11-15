import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('readonly',Path(__file__).resolve().parents[1]/'deploy/awg_readonly_bridge.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


class ReadonlyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.db=self.root/'clients.db';self.profiles=self.root/'profiles'
  with sqlite3.connect(self.db) as con:
   con.execute('CREATE TABLE clients(id INTEGER PRIMARY KEY,wg_client_id TEXT,name TEXT)')
   con.execute('INSERT INTO clients VALUES(7,?,?)',('legacy-link','Test'))
  folder=self.profiles/'7';folder.mkdir(parents=True)
  self.key='A'*43+'='
  (folder/'metadata.json').write_text(json.dumps(dict(client_id=7,public_key=self.key,address='10.0.0.7',private_key='must-never-leak')))
 def test_maps_numeric_metadata_to_existing_legacy_link_without_writes(self):
  before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
  result=bridge.collect(self.db,self.profiles,lambda:({self.key},{self.key:'123'},{self.key:['45','67']}))
  self.assertEqual(result,[dict(id='legacy-link',client_id=7,name='Test',profile_name='CAESAR BOT #7',address='10.0.0.7',enabled=True,latestHandshake=123,transferRx=45,transferTx=67)])
  self.assertNotIn(self.key,json.dumps(result));self.assertNotIn('must-never-leak',json.dumps(result))
  for path,data in before.items():self.assertEqual(path.read_bytes(),data)
 def test_profile_label_is_independent_of_customer_rename(self):
  with sqlite3.connect(self.db) as con:
   con.execute('CREATE TABLE awg_profile_labels(client_id INTEGER PRIMARY KEY,label_number INTEGER UNIQUE NOT NULL)')
   con.execute('INSERT INTO awg_profile_labels VALUES(7,42)')
   con.execute("UPDATE clients SET name='New customer name' WHERE id=7")
  result=bridge.collect(self.db,self.profiles,lambda:({self.key},{},{}))[0]
  self.assertEqual(result['name'],'New customer name')
  self.assertEqual(result['profile_name'],'CAESAR BOT #42')
 def test_missing_metadata_and_runtime_failure_are_errors_not_disabled_peers(self):
  (self.profiles/'7/metadata.json').unlink()
  with self.assertRaises(FileNotFoundError):bridge.collect(self.db,self.profiles,lambda:(set(),{},{}))
  with self.assertRaises(RuntimeError):bridge.collect(self.db,self.profiles,lambda:(_ for _ in ()).throw(RuntimeError()))
 def test_rejects_symlink_profile(self):
  folder=self.profiles/'7';folder.rename(self.profiles/'original');folder.symlink_to(self.profiles/'original')
  with self.assertRaises(ValueError):bridge.collect(self.db,self.profiles,lambda:(set(),{},{}))


if __name__=='__main__':unittest.main()
