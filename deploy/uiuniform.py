"""Guarded UI alignment and one-client INCY import pilot. No VPN changes."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import Request,build_opener,ProxyHandler,HTTPCookieProcessor
from urllib.error import HTTPError
from http.cookiejar import CookieJar
from urllib.parse import urlsplit
import hashlib,json,os,shutil,sqlite3,subprocess,tempfile,time,uuid
RELEASE="586c93ff533c90754556e97ef3f4261e4cdf2afb"
FILES=[["index.html","/opt/vpn-manager-production/public/index.html","51002d59bb1d5fff3508583d98bd6a97f8a735c4","30c95c8ae795643c04912dead0f801df62d32f6c"],["sqlite-manager.js","/opt/vpn-manager-production/public/sqlite-manager.js","6f31fc51e7f51ece08d4476196a1f1a2e999fd0a","c7dab3647ef90ba39f236dc03e18b2400f915579"],["server/connect.css","/opt/vpn-manager-connect/connect.css","a6fa077e9d8b506b00f600d68467615fbd8c5da0","5140c4211fe8f8020cb48c5cd9e0bd4598392d8e"],["server/connect.html","/opt/vpn-manager-connect/connect.html","f1acf74339da4a76888229805fe3e139c5874752","4990bb0645bed9c51e8dbd12256399173af2f7da"],["server/connect.js","/opt/vpn-manager-connect/connect.js","8e3eacba111a9a8712819b1c93fd0bd347dbe26f","d05c9de1d8de8142f589bbb54a22514a92ec7702"],["server/portal_service.py","/opt/vpn-manager-connect/portal_service.py","46cf3ca631a9836ee510d50ef542b4135a2fdb52","99808f5abb7164e23863ca3adfcfe9ec30921557"]]
ROOT=Path('/root/vpn-manager-backups')
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,b):
 fd,n=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o644);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(n,p)
def backup_db(p,target):
 with sqlite3.connect(target) as dest:
  with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as src:src.backup(dest)
def run(*args):return subprocess.run(args,capture_output=True,text=True,timeout=40,check=True).stdout.strip()
def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 os.umask(0o077);opener=build_opener(ProxyHandler({}));data={}
 for source,target,old,new in FILES:
  p=Path(target)
  if p.is_symlink() or blob(p.read_bytes()) not in (old,new):raise SystemExit('Source differs: '+source)
  with opener.open('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source,timeout=25) as r:b=r.read()
  assert blob(b)==new
  if source.endswith('.py'):compile(b,source,'exec')
  data[target]=b
 backup=ROOT/('ui-uniform-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700)
 for i,(_,target,_,_) in enumerate(FILES):shutil.copy2(target,backup/str(i))
 (backup/'files.json').write_text(json.dumps(FILES))
 db=Path('/opt/vpn-manager/data/clients-staging.db');backup_db(db,backup/'clients.db')
 backup_db(Path('/var/lib/vpn-manager-connect/state/links.db'),backup/'links.db')
 profiles={str(p):digest(p) for p in Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')}
 config=Path('/etc/amnezia/amneziawg/awg-test.conf');old_config=digest(config)
 xray=run('systemctl','show','xray.service','-p','MainPID','--value');bridge=run('systemctl','show','vpn-manager-live-bridge.service','-p','MainPID','--value')
 try:
  for target,b in data.items():write(Path(target),b)
  run('systemctl','restart','vpn-manager-connect.service')
  admin=Path('/etc/vpn-manager-connect-admin.env').read_text().strip().split('=',1)[1]
  def req(url,payload=None,headers=None,client=opener):
   h=dict(headers or {})
   if payload is not None:h['Content-Type']='application/json'
   with client.open(Request(url,data=None if payload is None else json.dumps(payload).encode(),headers=h),timeout=20) as r:return r.read()
  for _ in range(20):
   try:
    status=json.loads(req('http://127.0.0.1:5057/internal/status',headers={'X-Connect-Admin':admin}))
    if status['all_clients']:break
   except Exception:time.sleep(1)
  else:raise RuntimeError('Portal unavailable')
  def check(cid,pilot):
   issued=json.loads(req('http://127.0.0.1:5057/internal/link',dict(client_id=cid,action='issue'),{'X-Connect-Admin':admin,'Idempotency-Key':'uniform-check-'+uuid.uuid4().hex}))
   parts=urlsplit(issued['url']);payload=dict(handle=parts.path[1:],token=parts.fragment)
   client=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
   def post(path,extra=None):return req('https://connect.caesarbot.ru:8444/api/'+path,dict(payload,**(extra or {})),{'Origin':'https://connect.caesarbot.ru:8444'},client)
   login=json.loads(post('login',dict(code=issued['access_code'])))
   assert login['profile']['import_pilot'] is pilot
   if pilot:assert post('download')==(Path('/opt/vpn-manager/awg-clients')/str(cid)/'client.conf').read_bytes()
   post('logout')
  check(92,True);check(next(cid for cid in status['client_ids'] if cid!=92),False)
  h=req('https://185-23-239-142.sslip.io:8444/panel-live/').decode()
  assert 'v1.80' in h and h.index('id="desktopHeaderActions"')<h.index('</header>')
  with sqlite3.connect(backup/'clients.db') as old,sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as live:
   for table in ('clients','client_history'):assert old.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()==live.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()
  assert all(digest(Path(p))==d for p,d in profiles.items()) and digest(config)==old_config
  assert run('systemctl','show','xray.service','-p','MainPID','--value')==xray
  assert run('systemctl','show','vpn-manager-live-bridge.service','-p','MainPID','--value')==bridge
  print(json.dumps(dict(ok=True,version='1.80',desktop_toolbar_in_header=True,portal_buttons_aligned=True,incy_pilot_only=92,original_clients_unchanged=True,vpn_not_restarted=True,real_device_incy_import_pending=True,backup=str(backup))))
 except Exception:
  for i,(_,target,_,_) in enumerate(FILES):write(Path(target),(backup/str(i)).read_bytes())
  run('systemctl','restart','vpn-manager-connect.service')
  raise SystemExit('UI/portal code restored; current client DB and VPN profiles retained') from None
if __name__=='__main__':main()
