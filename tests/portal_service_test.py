import hashlib,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
from types import SimpleNamespace
from cryptography.fernet import Fernet
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from portal_service import create_app,ORIGIN

class PortalTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
  self.state=root/'state';self.material=root/'material';self.state.mkdir();self.material.mkdir()
  (self.state/'link.key').write_bytes(Fernet.generate_key())
  self.content=b'[Interface]\nPrivateKey = fixture-only\n[Peer]\nPublicKey = fixture\n'
  self.entries={str(cid):dict(number=number,period='1m',start_date='2026-01-01',end_date='2099-01-01',enabled=True,sha256=hashlib.sha256(self.content).hexdigest()) for cid,number in [(900,30),(901,82)]}
  for cid in self.entries:(self.material/(cid+'.conf')).write_bytes(self.content+ (b'' if cid=='900' else b'#other'))
  self.entries['901']['sha256']=hashlib.sha256((self.material/'901.conf').read_bytes()).hexdigest()
  self.save();self.config=dict(STATE=str(self.state),MATERIAL=str(self.material),ADMIN_TOKEN='a'*40,TESTING=True,CLASH_IMPORT_ENABLED=True)
  self.app=create_app(self.config);self.client=self.app.test_client();self.admin={'X-Connect-Admin':'a'*40};self.count=0
 def save(self,offset=0):
  (self.material/'portal.json').write_text(json.dumps(dict(version=2,updated_at=time.time()+offset,clients=self.entries)))
 def issue(self,cid=900,action='issue',key=None):
  self.count+=1
  return self.client.post('/internal/link',json=dict(client_id=cid,action=action),headers=dict(self.admin,**{'Idempotency-Key':key or 'portal-request-key-'+str(self.count)}))
 def parts(self,result):
  p=urlsplit(result['url']);return dict(handle=p.path[1:],token=p.fragment)
 def post(self,path,parts,**extra):return self.client.post('/api/'+path,json=dict(parts,**extra),headers={'Origin':ORIGIN})
 def login(self,result=None):
  result=result or self.issue().json;parts=self.parts(result)
  self.assertEqual(self.post('login',parts,code=result['access_code']).status_code,200)
  return result,parts
 def test_code_required_and_client_isolation(self):
  a=self.issue().json;b=self.issue(901).json;pa=self.parts(a);pb=self.parts(b)
  self.assertEqual(self.post('download',pa).status_code,401)
  self.assertEqual(self.post('login',pa,code=b['access_code']).status_code,401)
  self.login(a)
  self.assertEqual(self.post('profile',pa).json['profile']['label'],'CAESAR VPN #30')
  self.assertEqual(self.post('profile',pb).status_code,401)
  self.assertEqual(self.post('download',pa).data,self.content)
  self.assertEqual(self.post('download',dict(pa,token='z'*43)).status_code,401)
 def test_revocation_rotation_code_reset_invalidate_sessions(self):
  a,parts=self.login();self.issue(action='revoke')
  self.assertEqual(self.post('profile',parts).status_code,401)
  self.assertEqual(self.issue().status_code,409)
  b=self.issue(action='rotate').json;self.assertNotEqual(a['url'],b['url']);self.assertEqual(a['access_code'],b['access_code'])
  self.login(b);c=self.issue(action='reset-code').json
  self.assertEqual(c['url'],b['url']);self.assertNotEqual(c['access_code'],b['access_code'])
  self.assertEqual(self.post('profile',self.parts(b)).status_code,401)
  self.assertEqual(self.post('login',self.parts(c),code=b['access_code']).status_code,401)
  self.login(c)
 def test_stable_retries_restart_encryption_and_renewal(self):
  first=self.issue(key='stable-portal-request-1').json
  self.assertEqual(first,self.issue(key='stable-portal-request-1').json)
  self.assertEqual(self.issue(action='rotate',key='stable-portal-request-1').status_code,409)
  self.entries['900']['end_date']='2100-01-01';self.save()
  self.assertEqual(first,self.issue().json)
  restarted=create_app(self.config).test_client()
  r=restarted.post('/internal/link',json=dict(client_id=900,action='issue'),headers=dict(self.admin,**{'Idempotency-Key':'after-restart-portal-request'})).json
  self.assertEqual(first,r)
  raw=(self.state/'links.db').read_bytes()
  self.assertNotIn(first['access_code'].encode(),raw);self.assertNotIn(self.parts(first)['token'].encode(),raw)
 def test_expired_disabled_lifetime_progress_and_trial(self):
  a,parts=self.login()
  self.entries['900']['end_date']='2020-01-01';self.save()
  info=self.post('profile',parts).json['profile'];self.assertEqual(info['days_left'],0);self.assertEqual(info['progress'],0)
  self.assertEqual(self.post('download',parts).status_code,403)
  self.entries['900']['period']='lifetime';self.save()
  info=self.post('profile',parts).json['profile'];self.assertIsNone(info['days_left']);self.assertIsNone(info['end_date']);self.assertEqual(info['progress'],100)
  self.entries['900']['enabled']=False;self.save();self.assertEqual(self.post('download',parts).status_code,403)
  from datetime import datetime,timezone,timedelta
  self.entries['900'].update(enabled=True,period='trial',start_date=(datetime.now(timezone.utc)-timedelta(hours=23)).isoformat(),end_date=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat());self.save()
  info=self.post('profile',parts).json['profile'];self.assertEqual(info['days_left'],1);self.assertTrue(0<info['progress']<5)
 def test_stale_removed_corrupt_and_qr(self):
  a,parts=self.login()
  with patch('portal_service.subprocess.run',return_value=SimpleNamespace(stdout=b'<svg>fixture</svg>')) as run:
   self.assertEqual(self.post('qr',parts).data,b'<svg>fixture</svg>');self.assertEqual(run.call_args.kwargs['input'],self.content)
  (self.material/'900.conf').write_bytes(b'corrupt');self.assertEqual(self.post('download',parts).status_code,503)
  self.save(-151);self.assertEqual(self.post('profile',parts).status_code,503)
  del self.entries['900'];self.save();self.assertEqual(self.post('profile',parts).status_code,401)
 def test_lockout_survives_restart_and_session_expiry(self):
  a=self.issue().json;parts=self.parts(a)
  for _ in range(5):self.assertEqual(self.post('login',parts,code='wrong').status_code,401)
  restarted=create_app(self.config).test_client()
  r=restarted.post('/api/login',json=dict(parts,code=a['access_code']),headers={'Origin':ORIGIN});self.assertEqual(r.status_code,429)
  with sqlite3.connect(self.state/'links.db') as db:db.execute('UPDATE portal_attempts SET started=0')
  self.login(a)
  with sqlite3.connect(self.state/'links.db') as db:db.execute('UPDATE portal_sessions SET expires=0')
  self.assertEqual(self.post('profile',parts).status_code,401)
 def test_headers_origin_admin_and_generic_landing(self):
  a=self.issue().json;parts=self.parts(a);r=self.client.get('/'+parts['handle'])
  self.assertNotIn(a['access_code'].encode(),r.data);self.assertNotIn(b'fixture-only',r.data);self.assertNotIn(b'CAESAR VPN #30',r.data)
  self.assertIn('no-store',r.headers['Cache-Control']);self.assertIn('noindex',r.headers['X-Robots-Tag'])
  self.assertEqual(self.client.get('/internal/status').status_code,401)
  self.assertEqual(self.client.post('/api/login',json=parts,headers={'Origin':'https://evil.test'}).status_code,403)
  self.assertEqual(self.issue(999).status_code,404)
  self.login(a);r=self.post('login',parts,code=a['access_code']);cookie=r.headers['Set-Cookie'];self.assertIn('Secure',cookie);self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
 def test_direct_import_flag_is_only_disposable_pilot_and_still_requires_auth(self):
  self.entries['92']=dict(self.entries['900'],number=93);(self.material/'92.conf').write_bytes(self.content);self.save()
  normal,parts=self.login();self.assertFalse(self.post('profile',parts).json['profile']['import_pilot'])
  pilot=self.issue(92).json;pilot_parts=self.parts(pilot)
  self.assertEqual(self.post('download',pilot_parts).status_code,401)
  self.login(pilot);self.assertTrue(self.post('profile',pilot_parts).json['profile']['import_pilot'])
  self.entries['92']['enabled']=False;self.save();self.assertEqual(self.post('download',pilot_parts).status_code,403)

class ClashPortalTests(PortalTests):
 def setUp(self):
  super().setUp()
  from clash_export_test import CONF
  self.entries['92']=dict(self.entries['900'],number=93,sha256=hashlib.sha256(CONF).hexdigest())
  (self.material/'92.conf').write_bytes(CONF);self.save()
 def ticket(self):
  from urllib.parse import parse_qs
  result,parts=self.login(self.issue(92).json)
  response=self.post('clash-import',parts);self.assertEqual(response.status_code,200)
  uri=urlsplit(response.json['url']);self.assertEqual(uri.scheme,'clashmi');self.assertEqual(uri.netloc,'install-config')
  return parse_qs(uri.query)['url'][0],parts
 def test_import_auth_pilot_only_and_limits(self):
  a=self.issue(92).json;self.assertEqual(self.post('clash-import',self.parts(a)).status_code,401)
  normal,parts=self.login();self.assertEqual(self.post('clash-import',parts).status_code,403)
  url,parts=self.ticket();external=self.app.test_client()
  self.assertEqual(external.head(url).status_code,200)
  for _ in range(3):self.assertEqual(external.get(url).status_code,200)
  self.assertEqual(external.get(url).status_code,404)
  with sqlite3.connect(self.state/'links.db') as con:
   self.assertNotIn(urlsplit(url).query.split('=')[1],str(con.execute('SELECT * FROM portal_imports').fetchall()))
 def test_import_expires_revokes_and_rechecks_current_state(self):
  for change in ('expires','logout','revoke','reset-code','rotate','disabled','removed','stale','changed'):
   self.entries['92']['enabled']=True;self.save();self.issue(92,action='rotate')
   url,parts=self.ticket();external=self.app.test_client()
   if change=='expires':
    with sqlite3.connect(self.state/'links.db') as con:con.execute('UPDATE portal_imports SET expires=0')
   elif change=='logout':self.post('logout',parts)
   elif change in ('revoke','reset-code','rotate'):self.issue(92,action=change)
   elif change=='disabled':self.entries['92']['enabled']=False;self.save()
   elif change=='removed':
    entry=self.entries.pop('92');self.save()
   elif change=='stale':self.save(-151)
   else:
    (self.material/'92.conf').write_bytes(b'corrupt')
   self.assertNotEqual(external.get(url).status_code,200)
   if change=='removed':self.entries['92']=entry
 def test_feature_disabled_by_default(self):
  config=dict(self.config);config.pop('CLASH_IMPORT_ENABLED')
  app=create_app(config);self.assertFalse(app.config['CLASH_IMPORT_ENABLED'])
 def test_unsupported_export_fails_without_modifying_original(self):
  original=(self.material/'92.conf').read_bytes();bad=original.replace(b'MTU =',b'PostUp =')
  (self.material/'92.conf').write_bytes(bad);self.entries['92']['sha256']=hashlib.sha256(bad).hexdigest();self.save()
  result,parts=self.login(self.issue(92).json);self.assertEqual(self.post('clash-import',parts).status_code,503)
  self.assertEqual(self.post('download',parts).data,bad);self.assertEqual((self.material/'92.conf').read_bytes(),bad)

if __name__=='__main__':unittest.main()
