import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('portal_snapshot',Path(__file__).resolve().parents[1]/'deploy/portalsnapshot.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class SnapshotTests(unittest.TestCase):
 def test_snapshot_keeps_profile_bytes_and_excludes_archived_unmapped_and_symlinks(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);db=root/'clients.db';profiles=root/'profiles';material=root/'material';profiles.mkdir();material.mkdir()
   with sqlite3.connect(db) as con:
    con.execute('CREATE TABLE clients(id INTEGER,wg_client_id TEXT,period TEXT,start_date TEXT,end_date TEXT)')
    con.execute('CREATE TABLE awg_deleted_clients(client_id TEXT)')
    con.execute('CREATE TABLE awg_profile_labels(client_id INTEGER PRIMARY KEY,label_number INTEGER NOT NULL UNIQUE)')
    con.executemany('INSERT INTO awg_profile_labels VALUES(?,?)',[(1,30),(2,31),(4,33)])
    for cid in range(1,5):con.execute('INSERT INTO clients VALUES(?,?,?,?,?)',(cid,str(cid),'1m','2026-01-01','2099-01-01'))
    con.execute("INSERT INTO awg_deleted_clients VALUES('2')")
   payload=b'[Interface]\nPrivateKey = test-fixture\n'
   for cid in range(1,5):
    folder=profiles/str(cid);folder.mkdir();(folder/'metadata.json').write_text(json.dumps(dict(client_id=cid,enabled=cid!=1)));(folder/'client.conf').write_bytes(payload)
   (profiles/'4/client.conf').unlink();(profiles/'4/client.conf').symlink_to(profiles/'1/client.conf')
   original=db.read_bytes();hashes={str(p):p.read_bytes() for p in profiles.glob('*/*')}
   def writer(p,data,gid):p.write_bytes(data)
   with patch.object(module,'write',side_effect=writer):result=module.build(db,profiles,None,material,0)
   self.assertEqual(list(result),['1']);self.assertEqual(result['1']['number'],30);self.assertFalse(result['1']['enabled'])
   self.assertEqual((material/'1.conf').read_bytes(),payload);self.assertEqual(db.read_bytes(),original)
   for p,data in hashes.items():self.assertEqual(Path(p).read_bytes(),data)
   # A newly created, correctly mapped profile appears without touching VPN data.
   with patch.object(module,'write',side_effect=writer):result=module.build(db,profiles,{'1':30,'3':32},material,0)
   self.assertEqual(set(result),{'1','3'})

if __name__=='__main__':unittest.main()
