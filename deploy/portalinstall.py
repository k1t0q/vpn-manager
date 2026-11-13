"""Owner-requested portal rollout. No VPN restart, provisioning, or client DB writes."""
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
import fcntl,hashlib,json,os,shutil,socket,sqlite3,subprocess,sys,tempfile,time,uuid
RELEASE="2c623be3bb11006979676b714e025ecb0bcc8ca3"
FILES=json.loads("[[\"server/portal_service.py\",\"/opt/vpn-manager-connect/portal_service.py\",null,\"46cf3ca631a9836ee510d50ef542b4135a2fdb52\"],[\"server/connect.html\",\"/opt/vpn-manager-connect/connect.html\",\"f17f4c59f9bb1cb30864eef773ce9c9697a46d70\",\"4c788fcdb37eb4254198a84b348138f5a86350fb\"],[\"server/connect.js\",\"/opt/vpn-manager-connect/connect.js\",\"ebc84dd09f2832f381fa8c8715c10baf88f6ba5e\",\"29f35ac3efa58f84ec022ad987e1a377e6aa0ce7\"],[\"server/connect.css\",\"/opt/vpn-manager-connect/connect.css\",\"e49e68d4297895906a0508f9586401f8caa88a0f\",\"62cf08099faaf60ff7a28ade36e2c2fb67e97d59\"],[\"deploy/portalsnapshot.py\",\"/usr/local/libexec/vpn-manager-portal-snapshot.py\",null,\"ea1507636100dcb6b46f953179e3cca0b7491107\"],[\"index.html\",\"/opt/vpn-manager-production/public/index.html\",\"249945465587beec4c8681814d1e74451d5c5f81\",\"51002d59bb1d5fff3508583d98bd6a97f8a735c4\"],[\"sqlite-manager.js\",\"/opt/vpn-manager-production/public/sqlite-manager.js\",\"bc0511813aa5e7ec86c958faef4475cffe45a0fb\",\"6f31fc51e7f51ece08d4476196a1f1a2e999fd0a\"]]")
LEDGER=Path('/root/vpn-portal-install.json')
BASE=Path('/var/lib/vpn-manager-connect')
DROPINS=[Path('/etc/systemd/system/vpn-manager-connect.service.d/40-portal.conf'),Path('/etc/systemd/system/vpn-manager-connect-snapshot.service.d/40-portal.conf')]
DB=Path('/opt/vpn-manager/data/clients-staging.db')
opener=build_opener(ProxyHandler({}))
def run(*args):return subprocess.run([str(x) for x in args],capture_output=True,text=True,check=True,timeout=40).stdout.strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def write(p,b,mode=0o644):
 p.parent.mkdir(parents=True,exist_ok=True)
 fd,n=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),mode);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(n,p)
def save(s):write(LEDGER,json.dumps(s).encode(),0o600)
def backup_db(source,target):
 with sqlite3.connect(target) as dest:
  with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src:src.backup(dest)
  assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
def request(url,data=None,headers=None):
 h=dict(headers or {})
 if data is not None:h['Content-Type']='application/json'
 with opener.open(Request(url,data=None if data is None else json.dumps(data).encode(),headers=h),timeout=25) as r:return r.read()
def rollback(s):
 for unit in ('vpn-manager-connect-snapshot.timer','vpn-manager-connect.service'):
  subprocess.run(['systemctl','stop',unit],capture_output=True)
 root=Path(s['backup'])
 for item in s['files']:
  p=Path(item['path'])
  if item['existed']:write(p,(root/item['saved']).read_bytes(),item['mode'])
  elif p in DROPINS and p.exists():p.unlink()
 run('systemctl','daemon-reload')
 s['phase']='rolled-back';save(s)
 print('PORTAL_STOPPED_OLD_UI_RESTORED; current client DB and VPN profiles retained')
def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 os.umask(0o077)
 state=json.loads(LEDGER.read_text()) if LEDGER.exists() else None
 if '--rollback' in sys.argv:
  if not state:raise SystemExit('No portal ledger')
  return rollback(state)
 if state and state['phase']=='complete':print(json.dumps(state['report']));return
 if not shutil.which('qrencode'):raise SystemExit('Existing QR renderer required')
 if Path('/var/lib/vpn-manager-production/operations/pending.json').exists():raise SystemExit('Pending VPN operation; inspect first')
 materials={}
 for source,target,old,new in FILES:
  p=Path(target)
  if p.is_symlink() or (p.exists() and blob(p.read_bytes()) not in (old,new)) or (old and not p.exists()):raise SystemExit('Unexpected source: '+source)
  with opener.open('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source,timeout=30) as r:b=r.read()
  if blob(b)!=new:raise SystemExit('Digest mismatch')
  if source.endswith('.py'):compile(b,source,'exec')
  materials[target]=b
 if state is None:
  root=Path('/root/vpn-manager-backups')/('portal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));root.mkdir(mode=0o700)
  files=[]
  for i,p in enumerate([Path(x[1]) for x in FILES]+DROPINS):
   item=dict(path=str(p),existed=p.exists(),saved=str(i),mode=p.stat().st_mode & 0o777 if p.exists() else 0o644)
   if p.exists():shutil.copy2(p,root/str(i))
   files.append(item)
  backup_db(DB,root/'clients.db');backup_db(BASE/'state/links.db',root/'links-before.db');shutil.copy2(BASE/'state/link.key',root/'link.key')
  profiles={str(p):sha(p) for p in Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')}
  state=dict(backup=str(root),files=files,phase='prepared',profiles=profiles,awg=sha(Path('/etc/amnezia/amneziawg/awg-test.conf')),ifindex=socket.if_nametoindex('awg-test'),peers=run('awg','show','awg-test','peers'),xray=run('systemctl','show','xray.service','-p','MainPID','--value'),bridge=run('systemctl','show','vpn-manager-live-bridge.service','-p','MainPID','--value'))
  save(state)
 root=Path(state['backup'])
 try:
  run('systemctl','stop','vpn-manager-connect-snapshot.timer');run('systemctl','stop','vpn-manager-connect-snapshot.service')
  for target,b in materials.items():
   if '/public/' not in target:write(Path(target),b,0o600 if target.startswith('/usr/local/libexec/') else 0o644)
  write(DROPINS[0],b'[Service]\nExecStart=\nExecStart=/opt/vpn-manager-production/venv/bin/python -m gunicorn --workers 1 --bind 127.0.0.1:5057 portal_service:app\n')
  write(DROPINS[1],b'[Service]\nExecStart=\nExecStart=/usr/bin/python3 /usr/local/libexec/vpn-manager-portal-snapshot.py\nEnvironmentFile=/etc/vpn-manager-live-bridge.env\n')
  run('systemctl','daemon-reload')
  state['phase']='snapshot';save(state);run('systemctl','start','vpn-manager-connect-snapshot.service')
  current=json.loads((BASE/'material/portal.json').read_text());assert current['version']==2
  with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as con:
   ids={str(r[0]) for r in con.execute('SELECT id FROM clients WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients) AND wg_client_id IS NOT NULL')}
  if set(current['clients'])!=ids:raise RuntimeError('Not all current profiles have an exact manager label')
  state['phase']='portal';save(state);run('systemctl','restart','vpn-manager-connect.service')
  token=Path('/etc/vpn-manager-connect-admin.env').read_text().strip().split('=',1)[1]
  headers={'X-Connect-Admin':token}
  for _ in range(20):
   try:
    status=json.loads(request('http://127.0.0.1:5057/internal/status',headers=headers))
    if status.get('all_clients'):break
   except Exception:time.sleep(1)
  else:raise RuntimeError('Portal backend unavailable')
  assert set(map(str,status['client_ids']))==ids
  run('systemctl','start','vpn-manager-connect-snapshot.timer')
  pilot=json.loads(Path('/root/vpn-connect-pilot-install.json').read_text())['client_id']
  if str(pilot) not in ids:raise RuntimeError('Existing test client unavailable')
  result=json.loads(request('http://127.0.0.1:5057/internal/link',dict(client_id=pilot,action='issue'),dict(headers,**{'Idempotency-Key':'portal-rollout-'+uuid.uuid4().hex})))
  from urllib.parse import urlsplit
  from http.cookiejar import CookieJar
  from urllib.request import HTTPCookieProcessor
  jar=CookieJar();public=build_opener(ProxyHandler({}),HTTPCookieProcessor(jar));parts=urlsplit(result['url']);payload=dict(handle=parts.path[1:],token=parts.fragment)
  def post(path,data):
   req=Request('https://connect.caesarbot.ru:8444/api/'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','Origin':'https://connect.caesarbot.ru:8444'})
   with public.open(req,timeout=25) as r:return r.read()
  try:post('download',payload)
  except HTTPError as error:assert error.code==401
  else:raise RuntimeError('Code gate bypassed')
  logged=json.loads(post('login',dict(payload,code=result['access_code'])));assert logged['profile']['label']==result['label']
  assert post('download',payload)==(Path('/opt/vpn-manager/awg-clients')/str(pilot)/'client.conf').read_bytes()
  assert b'<svg' in post('qr',payload)
  post('logout',payload)
  # Publish the manager controls only after the existing isolated test client
  # has passed HTTPS code-gate, exact-byte download and QR checks.
  for target,b in materials.items():
   if '/public/' in target:write(Path(target),b)
  with sqlite3.connect(BASE/'state/links.db') as con:
   rows=con.execute('SELECT client_id,handle FROM links WHERE client_id IN ('+','.join('?' for _ in ids)+')',tuple(map(int,ids))).fetchall()
   assert len(rows)==len(ids) and len({r[1] for r in rows})==len(ids)
   assert con.execute('SELECT COUNT(*) FROM portal_codes').fetchone()[0]>=len(ids)
  assert all(Path(p).exists() and sha(Path(p))==digest for p,digest in state['profiles'].items())
  assert sha(Path('/etc/amnezia/amneziawg/awg-test.conf'))==state['awg']
  assert socket.if_nametoindex('awg-test')==state['ifindex']
  assert run('awg','show','awg-test','peers')==state['peers']
  assert run('systemctl','show','xray.service','-p','MainPID','--value')==state['xray']
  assert run('systemctl','show','vpn-manager-live-bridge.service','-p','MainPID','--value')==state['bridge']
  with sqlite3.connect(root/'clients.db') as old,sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as live:
   for table in ('clients','client_history'):assert old.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()==live.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()
  backup_db(BASE/'state/links.db',root/'links-after.db')
  assert b'v1.79' in request('https://185-23-239-142.sslip.io:8444/panel-live/')
  state['phase']='complete';state['report']=dict(ok=True,version='1.79',client_portals=len(ids),code_gate_verified=True,test_profile_download_identical=True,qr_verified=True,existing_clients_unchanged=True,awg_xray_not_restarted=True,backup=str(root),automatic_app_import=False)
  save(state);print(json.dumps(state['report'],indent=2))
 except Exception as error:
  failed=state['phase'];rollback(state);print(json.dumps(dict(ok=False,phase=failed,error_type=type(error).__name__,backup=state['backup'])))
  raise SystemExit('Portal activation stopped safely; inspect private backup') from None
if __name__=='__main__':
 with open('/root/vpn-portal-install.lock','a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);main()

