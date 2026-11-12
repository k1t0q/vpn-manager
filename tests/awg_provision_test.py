import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import awg_persistence_test as base
from awg_provision import Provisioner,deadline
from awg_persistence import StateError


def key(value):return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


class Runtime(base.Runtime):
 def __init__(self,keys):super().__init__(keys);self.generated=0;self.extra=[]
 def allowed_ips(self):return [ipaddress.ip_network('10.99.0.'+str(i)+'/32') for i in [*range(1,61),200,*self.extra]]
 def server_public_key(self):return key('server')
 def new_keys(self):
  self.generated+=1
  return tuple(key(kind+str(self.generated)) for kind in ['private','public','psk'])


class ProvisionTests(unittest.TestCase):
 def setUp(self):
  base.PersistenceTests.setUp(self)
  self.original=self.original.replace('[Interface]\n','[Interface]\nAddress = 10.99.0.254/24\n')
  self.config.write_text(self.original)
  self.runtime=Runtime(self.runtime.keys)
  with sqlite3.connect(self.db) as con:
   for definition in ['phone TEXT','start_date TEXT','end_date TEXT','period TEXT','note TEXT','referrer_id INTEGER','wg_client_id TEXT']:
    con.execute('ALTER TABLE clients ADD COLUMN '+definition)
   con.execute("UPDATE clients SET period='1m',start_date='2026-01-01',end_date='2027-01-01'")
  self.template=self.root/'template.conf'
  self.template.write_text('[Interface]\nPrivateKey = original-client-private\nAddress = 10.99.0.60/32\nJc = 3\nDNS = 1.1.1.1\n\n[Peer]\nPublicKey = '+key('server')+'\nPresharedKey = original-client-psk\nEndpoint = 185.23.239.142:51840\nAllowedIPs = 0.0.0.0/0\n')
  self.before_profiles={p:hashlib.sha256(p.read_bytes()).digest() for p in self.profiles.rglob('*') if p.is_file()}
  self.store=Provisioner(self.db,self.config,self.profiles,self.root/'state',self.runtime,protected_ids=range(1,61))
  self.data=dict(name='Disposable test',phone='123',note='Keep this note',start_date='2026-11-12',period='1m')
  self.now=datetime(2026,11,12,21,43,tzinfo=timezone.utc)
 def tearDown(self):base.PersistenceTests.tearDown(self)
 def create(self,request_id='create-test-00001',data=None):return self.store.create(data or self.data,request_id,self.template,now=self.now)
 def count(self):
  with sqlite3.connect(self.db) as con:return con.execute('SELECT count(*) FROM clients').fetchone()[0]
 def assert_originals(self,changed_metadata=()):
  for path,digest in self.before_profiles.items():
   if path.name=='metadata.json' and path.parent.name in changed_metadata:continue
   self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),digest)
  for public in self.keys.values():self.assertIn(public,self.runtime.keys)
  self.assertIn('unmanaged-test-peer',self.runtime.keys)
 def test_create_new_profile_and_retry_without_rotation(self):
  result=self.create();self.assertEqual(result['id'],'61');self.assertEqual(result['address'],'10.99.0.61')
  self.assertEqual(self.create(),result);self.assertEqual(self.runtime.generated,1);self.assertEqual(self.count(),61)
  config=(self.profiles/'61/client.conf').read_text()
  self.assertNotIn('original-client-private',config);self.assertNotIn('original-client-psk',config)
  self.assertIn('Jc = 3',config);self.assertIn('Endpoint = 185.23.239.142:51840',config)
  with sqlite3.connect(self.db) as con:self.assertEqual(con.execute('SELECT note,wg_client_id FROM clients WHERE id=61').fetchone(),('Keep this note','61'))
  self.assertEqual((self.profiles/'61/client.conf').stat().st_mode & 0o077,0)
  self.assert_originals()
 def test_lost_success_response_does_not_create_second_profile_or_bonus(self):
  self.data['referrer_id']=1
  def fail(point):
   if point=='db_committed':raise RuntimeError('lost reply')
  self.store.fault=fail
  with self.assertRaises(RuntimeError):self.create()
  self.store.fault=lambda p:None
  self.assertEqual(self.create()['id'],'61');self.assertEqual(self.runtime.generated,1)
  with sqlite3.connect(self.db) as con:
   self.assertEqual(con.execute('SELECT count(*) FROM referral_awards').fetchone()[0],1)
   self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=1').fetchone()[0],'2027-01-15')
  self.assertEqual(json.loads((self.profiles/'1/metadata.json').read_text())['end_date'],'2027-01-15')
  self.assert_originals(changed_metadata=('1',))
 def test_referral_metadata_recovers_with_transaction(self):
  self.data['referrer_id']=1
  before=(self.profiles/'1/metadata.json').read_bytes()
  self.store.fault=lambda p:(_ for _ in ()).throw(RuntimeError('crash')) if p=='referral_metadata_written' else None
  with self.assertRaises(RuntimeError):self.create()
  self.store.fault=lambda p:None
  self.assertEqual(self.store.recover(),'before')
  self.assertEqual((self.profiles/'1/metadata.json').read_bytes(),before)
  with sqlite3.connect(self.db) as con:self.assertEqual(con.execute('SELECT end_date FROM clients WHERE id=1').fetchone()[0],'2027-01-01')
  self.assert_originals()
 def test_partial_failures_restore_previous_database_disk_and_runtime(self):
  for point in ['prepared','profile_written','config_written','runtime_applied']:
   with self.subTest(point=point):
    self.store.fault=lambda p:(_ for _ in ()).throw(RuntimeError('crash')) if p==point else None
    with self.assertRaises(RuntimeError):self.create('fail-'+point+'-00001')
    self.store.fault=lambda p:None
    self.assertEqual(self.store.recover(),'before')
    self.assertEqual(self.count(),60);self.assertEqual(self.config.read_text(),self.original)
    self.assertFalse((self.profiles/'61').exists());self.assertEqual(len(self.runtime.keys),61)
    self.assert_originals()
 def test_live_only_address_is_reserved(self):
  self.runtime.extra=[61]
  self.assertEqual(self.create()['address'],'10.99.0.62');self.assert_originals()
 def test_trial_is_exactly_24_hours_and_lifetime_has_no_deadline(self):
  result=self.create(data={**self.data,'period':'trial'})
  with sqlite3.connect(self.db) as con:
   value=con.execute('SELECT end_date FROM clients WHERE id=?',(result['id'],)).fetchone()[0]
  self.assertEqual(datetime.fromisoformat(value)-self.now,__import__('datetime').timedelta(hours=24))
  self.assertIsNone(deadline(self.now.date(),'lifetime',self.now))
  self.assertEqual(deadline(__import__('datetime').date(2026,1,31),'1m',self.now),'2026-02-28')
 def test_concurrent_retries_have_one_profile(self):
  with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:self.create(),range(3)))
  self.assertEqual(len({r['id'] for r in results}),1);self.assertEqual(self.runtime.generated,1);self.assert_originals()
 def test_bad_request_and_unavailable_awg_leave_originals_unchanged(self):
  with self.assertRaises(StateError):self.create(data={**self.data,'period':'unknown'})
  self.runtime.available=False
  with self.assertRaises(StateError):self.create()
  self.assertEqual(self.count(),60);self.assertEqual(self.config.read_text(),self.original);self.assertEqual(self.runtime.generated,0)
 def test_all_referral_tariffs_and_lifetime_referrer(self):
  for n,(period,bonus) in enumerate([('1m',14),('6m',30),('12m',60)],1):
   self.create('referral-tariff-000'+str(n),{**self.data,'period':period,'referrer_id':n})
   with sqlite3.connect(self.db) as con:
    recorded=con.execute('SELECT bonus_days FROM referral_awards WHERE referrer_client_id=?',(n,)).fetchone()[0]
   self.assertEqual(recorded,bonus)
  with sqlite3.connect(self.db) as con:con.execute("UPDATE clients SET period='lifetime',end_date=NULL WHERE id=4")
  self.create('lifetime-referral-01',{**self.data,'referrer_id':4})
  with sqlite3.connect(self.db) as con:self.assertEqual(con.execute('SELECT period,end_date FROM clients WHERE id=4').fetchone(),('lifetime',None))


if __name__=='__main__':unittest.main()
