from datetime import datetime,timedelta,timezone
import json,sqlite3,unittest
import awg_provision_test as base
from awg_persistence import StateError,peer_blocks
from awg_subscriptions import Subscriptions


class SubscriptionTests(unittest.TestCase):
 def setUp(self):
  base.ProvisionTests.setUp(self)
  self.clock=datetime(2026,11,12,12,tzinfo=timezone.utc)
  self.store=Subscriptions(self.db,self.config,self.profiles,self.root/'state',self.runtime,protected_ids=range(1,60))
 def tearDown(self):base.ProvisionTests.tearDown(self)
 def change(self,kind,data,key='subscription-test-01'):return self.store.change(60,kind,data,key,self.clock)
 def row(self,cid=60):
  with self.store.connect() as con:return dict(con.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone())
 def meta(self):return json.loads((self.profiles/'60/metadata.json').read_text())
 def test_calendar_renewal_and_lifetime_never_shortens(self):
  self.assertEqual(self.change('extend',{'period':'1m'})['end_date'],'2027-02-01')
  result=self.change('extend',{'period':'lifetime'},'renew-lifetime-0001');self.assertIsNone(result['end_date'])
  result=self.change('extend',{'period':'12m'},'renew-infinity-0001');self.assertEqual(result['period'],'lifetime');self.assertIsNone(result['end_date'])
  self.assertIsNone(self.meta()['end_date'])
 def test_trial_exact_expiry_and_auto_reactivation_after_renewal(self):
  result=self.change('extend',{'period':'trial'})
  end=datetime.fromisoformat(result['end_date']);self.assertEqual(end-self.clock,timedelta(hours=24))
  self.assertEqual(self.store.tick(end-timedelta(seconds=1)),[])
  self.store.tick(end)
  self.assertNotIn(self.keys[60],self.runtime.keys);self.assertTrue(self.meta()['auto_disabled'])
  with self.assertRaises(StateError):self.store.toggle(60,True,'expired-manual-on-1',now=end)
  result=self.store.change(60,'extend',{'period':'1m'},'renew-after-expiry1',end)
  self.assertTrue(result['enabled']);self.assertIn(self.keys[60],self.runtime.keys)
  self.assertFalse(self.meta()['auto_disabled'])
 def test_manual_disable_remains_disabled_after_renewal(self):
  self.store.toggle(60,False,'manually-disable-1')
  result=self.change('extend',{'period':'6m'});self.assertFalse(result['enabled'])
  self.assertNotIn(self.keys[60],self.runtime.keys)
 def test_notes_phone_and_name_preserve_other_peers_and_config_material(self):
  before=self.config.read_bytes()
  self.change('edit',{'name':'Updated','phone':'123','note':'Preserved note'})
  self.assertEqual(self.row()['note'],'Preserved note');self.assertEqual(self.meta()['name'],'Updated')
  self.assertEqual(self.config.read_bytes(),before)
  for cid in range(1,60):self.assertIn(self.keys[cid],self.runtime.keys)
 def test_duplicate_renewal_after_lost_success_adds_month_once(self):
  self.store.fault=lambda p:(_ for _ in ()).throw(RuntimeError('lost reply')) if p=='db_committed' else None
  with self.assertRaises(RuntimeError):self.change('extend',{'period':'1m'})
  self.store.fault=lambda p:None
  self.assertEqual(self.change('extend',{'period':'1m'})['end_date'],'2027-02-01')
  self.assertEqual(self.row()['end_date'],self.meta()['end_date'])
 def test_partial_changes_restore_db_metadata_config_and_peer(self):
  original=self.row();original_meta=(self.profiles/'60/metadata.json').read_bytes();original_config=self.config.read_bytes()
  for point in ['prepared','config_written','metadata_written','runtime_applied']:
   with self.subTest(point=point):
    self.store.fault=lambda p:(_ for _ in ()).throw(RuntimeError('crash')) if p==point else None
    with self.assertRaises(RuntimeError):self.change('edit',{'end_date':'2026-11-01'},'sub-crash-'+point+'-1')
    self.store.fault=lambda p:None;self.store.recover()
    self.assertEqual(self.row(),original);self.assertEqual((self.profiles/'60/metadata.json').read_bytes(),original_meta)
    self.assertEqual(self.config.read_bytes(),original_config);self.assertIn(self.keys[60],self.runtime.keys)
 def test_notifications_are_deduplicated_and_expiry_ignores_protected_ids(self):
  self.change('edit',{'end_date':'2026-11-14'})
  self.store.tick(self.clock);self.store.tick(self.clock)
  with self.store.connect() as con:self.assertEqual(con.execute("SELECT count(*) FROM subscription_notifications WHERE event_type='three_days'").fetchone()[0],1)
  self.store.tick(self.clock+timedelta(days=3));self.store.tick(self.clock+timedelta(days=3))
  with self.store.connect() as con:
   self.assertEqual(con.execute("SELECT count(*) FROM subscription_notifications WHERE event_type='expired'").fetchone()[0],1)
   self.assertEqual(con.execute("SELECT count(*) FROM client_history WHERE event_type='expired'").fetchone()[0],1)
  for cid in range(1,60):self.assertIn(self.keys[cid],self.runtime.keys)
 def test_awg_unavailable_leaves_subscription_unchanged(self):
  before=self.row();self.runtime.available=False
  with self.assertRaises(StateError):self.change('extend',{'period':'1m'})
  self.assertEqual(self.row(),before)
 def test_referral_tariffs_once_and_lifetime_stays_infinite(self):
  for number,(period,days) in enumerate([('1m',14),('6m',30),('12m',60)],61):
   with self.store.connect() as con:
    con.execute('INSERT INTO clients(id,user_id,name,period,end_date,referrer_id) VALUES(?,?,?,?,?,?)',(number,'owner','Test referred',period,'2027-01-01',60))
   before=datetime.fromisoformat(self.row()['end_date'])
   result=self.change('referral',{'client_id':number,'bonus_period':period},'referral-manual-'+str(number))
   self.assertEqual(datetime.fromisoformat(result['end_date'])-before,timedelta(days=days))
   self.assertEqual(self.change('referral',{'client_id':number,'bonus_period':period},'referral-manual-'+str(number)),result)
   with self.assertRaises(StateError):self.change('referral',{'client_id':number,'bonus_period':period},'referral-repeat-'+str(number))
  self.change('extend',{'period':'lifetime'},'ref-lifetime-00001')
  with self.store.connect() as con:con.execute("INSERT INTO clients(id,user_id,name,period,end_date,referrer_id) VALUES(64,'owner','Test lifetime','1m','2027-01-01',60)")
  self.assertIsNone(self.change('referral',{'client_id':64},'ref-lifetime-00002')['end_date'])


if __name__=='__main__':unittest.main()
