import sys,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from staging_api import create_app
from sqlite_client_crud import end_date
from datetime import date

class PanelTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=self.tmp.name+'/test.db'
  with sqlite3.connect(self.db) as c:
   c.executescript('CREATE TABLE clients(id INTEGER PRIMARY KEY,user_id TEXT,name TEXT,phone TEXT,start_date TEXT,end_date TEXT,period TEXT,note TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,referrer_id INTEGER,wg_client_id TEXT);CREATE TABLE client_history(id INTEGER PRIMARY KEY,user_id TEXT,client_id INTEGER,event_type TEXT,description TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);')
   for i in range(1,60):c.execute('INSERT INTO clients(id,user_id,name,start_date,end_date,period,note,wg_client_id,referrer_id) VALUES(?,?,?,?,?,?,?,?,?)',(i,'owner','Synthetic '+str(i),'2026-01-01','2026-02-01','1m','keep note','peer-'+str(i),1 if i==2 else None))
  self.app=create_app({'TESTING':True,'DB_PATH':self.db,'ADMIN_CODE':'test-code','AWG_TOKEN':'test-upstream'});self.c=self.app.test_client()
  token=self.c.post('/auth/login',json={'code':'test-code'}).json['access_token'];self.h={'Authorization':'Bearer '+token}
 def tearDown(self):self.tmp.cleanup()
 def test_auth_and_cors(self):
  self.assertEqual(self.c.get('/api/clients').status_code,401)
  r=self.c.options('/api/clients/1',headers={'Origin':'https://k1t0q.github.io'});self.assertIn('PATCH',r.headers['Access-Control-Allow-Methods'])
  self.assertEqual(self.c.get('/api/clients',headers={**self.h,'Origin':'https://evil.invalid'}).status_code,403)
  self.c.post('/auth/logout',headers=self.h);self.assertEqual(self.c.get('/api/clients',headers=self.h).status_code,401)
 def test_59_rows_and_no_vpn_side_effects(self):
  with patch('staging_api.requests.request') as awg:
   rows=self.c.get('/api/clients',headers=self.h).json['clients'];self.assertEqual(len(rows),59)
   self.assertNotIn('user_id',rows[0])
   self.assertEqual(self.c.patch('/api/clients/1',headers=self.h,json={'name':'Edited'}).status_code,200)
   self.assertEqual(self.c.post('/api/clients/1/extend',headers=self.h,json={'period':'lifetime'}).status_code,200)
   self.assertEqual(self.c.post('/api/clients/1/extend',headers=self.h,json={'period':'1m'}).status_code,409)
   awg.assert_not_called()
  with sqlite3.connect(self.db) as c:self.assertEqual(c.execute('SELECT note,wg_client_id,end_date FROM clients WHERE id=1').fetchone(),('keep note','peer-1',None))
  self.assertEqual(len(self.c.get('/api/clients/1/history',headers=self.h).json['history']),2)
 def test_create_delete_and_referrals(self):
  r=self.c.post('/api/clients/create',headers=self.h,json={'name':'New','start_date':'2026-11-12','period':'1m','user_id':'evil'})
  self.assertEqual(r.status_code,201);new=r.json['id']
  with sqlite3.connect(self.db) as c:self.assertEqual(c.execute('SELECT user_id FROM clients WHERE id=?',(new,)).fetchone()[0],'owner')
  self.assertEqual(self.c.post('/api/clients/1/delete',headers=self.h).status_code,409)
  self.assertEqual(self.c.post('/api/clients/'+str(new)+'/delete',headers=self.h).status_code,200)
  r=self.c.post('/api/referrals/award-safe',headers=self.h,json={'client_id':2});self.assertEqual(r.status_code,200)
  self.assertEqual(self.c.post('/api/referrals/award-safe',headers=self.h,json={'client_id':2}).status_code,409)
 def test_bridge_and_route_uniqueness(self):
  with patch('staging_api.requests.request',return_value=Mock(ok=True,status_code=200,content=b'QR',headers={'Content-Type':'image/png'})) as upstream:
   r=self.c.get('/api/wg/client/peer-1/qr',headers=self.h);self.assertEqual(r.data,b'QR');self.assertEqual(r.headers['Cache-Control'],'no-store')
   self.assertEqual(upstream.call_args.kwargs['headers'],{'X-VPN-Token':'test-upstream'})
  routes=[(r.rule,m) for r in self.app.url_map.iter_rules() for m in r.methods if m not in ('HEAD','OPTIONS')];self.assertEqual(len(routes),len(set(routes)))
  self.assertEqual(end_date(date(2026,1,31),'1m'),'2026-02-28')

if __name__=='__main__':unittest.main()
