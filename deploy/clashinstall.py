"""Pinned portal-only Clash Mi pilot installation with private rollback backups."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import Request,build_opener,ProxyHandler,HTTPCookieProcessor
from urllib.parse import urlsplit,parse_qs
from http.cookiejar import CookieJar
from urllib.error import HTTPError
import hashlib,json,os,shutil,sqlite3,subprocess,tempfile,time,uuid
RELEASE='2c397c96f251ae626a811521ebc290681c7bea3f'
FILES=[['server/connect.html', '/opt/vpn-manager-connect/connect.html', '4990bb0645bed9c51e8dbd12256399173af2f7da', 'c790329157c294e94047677d2557b3fd4e70c930'], ['server/connect.js', '/opt/vpn-manager-connect/connect.js', 'a80122c15c0845e7b94a3598d9b9ee570616c4ef', '64f69890e3d75e36946a869294b06b3a82661f1a'], ['server/portal_service.py', '/opt/vpn-manager-connect/portal_service.py', '99808f5abb7164e23863ca3adfcfe9ec30921557', 'a1a1cbc8f35169f756ff2e8c83a52d3e866e5340'], ['server/clash_export.py', '/opt/vpn-manager-connect/clash_export.py', None, 'cae8b7ef9eef33625c6837915b5bc09348c28bab']]
CADDY=Path('/etc/caddy/Caddyfile')
ENV=Path('/etc/systemd/system/vpn-manager-connect.service.d/50-clash-pilot.conf')
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(*args):return subprocess.run(args,capture_output=True,text=True,timeout=40,check=True).stdout.strip()
def write(p,b,mode=0o644):
 fd,n=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),mode);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(n,p)
def backup_db(p,target):
 with sqlite3.connect(target) as dest:
  with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as src:src.backup(dest)
def comparable(value):
 # Caddy adds the source Caddyfile to file_server.hide. Its adapter emits
 # this set in non-deterministic order; only normalize that documented field.
 value=json.loads(json.dumps(value))
 def walk(item):
  if isinstance(item,dict):
   if item.get('handler')=='file_server' and isinstance(item.get('hide'),list):
    item['hide']=sorted('/etc/caddy/Caddyfile' if x=='/etc/caddy/clash-candidate.caddy' else x for x in item['hide'])
   for child in item.values():walk(child)
  elif isinstance(item,list):
   for child in item:walk(child)
 walk(value);return value
def main():
 if os.geteuid()!=0:raise RuntimeError('Root required')
 os.umask(0o077)
 assert Path('/sys/class/net/awg-test').exists()
 assert '185.23.239.142' in run('ip','-4','addr','show')
 opener=build_opener(ProxyHandler({}));data={}
 for source,target,old,new in FILES:
  p=Path(target)
  assert not p.is_symlink()
  assert (not p.exists()) if old is None else blob(p.read_bytes())==old
  with opener.open('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source,timeout=25) as r:b=r.read()
  assert blob(b)==new
  if source.endswith('.py'):compile(b,source,'exec')
  data[target]=b
 namespace={};exec(compile(data['/opt/vpn-manager-connect/clash_export.py'],'clash_export.py','exec'),namespace)
 original=Path('/opt/vpn-manager/awg-clients/92/client.conf').read_bytes()
 exported=namespace['convert'](original,'CAESAR VPN TEST')
 assert b'type' in exported and original==Path('/opt/vpn-manager/awg-clients/92/client.conf').read_bytes()
 caddyold=CADDY.read_bytes();assert caddyold.lstrip().startswith(b'{') and not ENV.exists()
 adapted=json.loads(run('caddy','adapt','--config',str(CADDY),'--adapter','caddyfile'))
 assert not adapted.get('logging')
 pid=run('systemctl','show','vpn-manager-connect.service','-p','MainPID','--value')
 assert 'access-logfile' not in Path('/proc/'+pid+'/cmdline').read_bytes().decode()
 addition=b'\n log default {\n  format filter {\n   wrap json\n   request>uri query {\n    replace ticket REDACTED\n   }\n  }\n }\n'
 caddynew=caddyold.replace(b'{',b'{'+addition,1)
 candidate=Path('/etc/caddy/clash-candidate.caddy');write(candidate,caddynew)
 try:
  after=json.loads(run('caddy','adapt','--config',str(candidate),'--adapter','caddyfile'))
  logging=after.pop('logging');assert 'ticket' in json.dumps(logging) and 'REDACTED' in json.dumps(logging)
  assert comparable(after)==comparable(adapted)
  run('caddy','validate','--config',str(candidate),'--adapter','caddyfile')
 finally:candidate.unlink(missing_ok=True)
 backup=Path('/root/vpn-manager-backups')/('clash-mi-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700)
 paths=[target for _,target,_,_ in FILES]+[str(CADDY),str(ENV)]
 manifest=[]
 for i,target in enumerate(paths):
  p=Path(target);entry=dict(path=target,existed=p.exists())
  if p.exists():shutil.copy2(p,backup/str(i));entry['mode']=p.stat().st_mode&0o777
  manifest.append(entry)
 (backup/'files.json').write_text(json.dumps(manifest))
 db=Path('/opt/vpn-manager/data/clients-staging.db');backup_db(db,backup/'clients.db')
 backup_db(Path('/var/lib/vpn-manager-connect/state/links.db'),backup/'links.db')
 shutil.copy2('/var/lib/vpn-manager-connect/state/link.key',backup/'link.key')
 profiles={str(p):digest(p) for p in Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')}
 config=Path('/etc/amnezia/amneziawg/awg-test.conf');confighash=digest(config)
 protected={name:run('systemctl','show',name,'-p','MainPID','--value') for name in ['xray.service','vpn-manager-live-bridge.service']}
 peers=run('awg','show','awg-test','peers')
 def request(url,payload=None,headers=None,client=opener):
  h=dict(headers or {})
  if payload is not None:h['Content-Type']='application/json'
  with client.open(Request(url,data=None if payload is None else json.dumps(payload).encode(),headers=h),timeout=20) as r:return r.read()
 def restore():
  for i,entry in enumerate(manifest):
   p=Path(entry['path'])
   if entry['existed']:write(p,(backup/str(i)).read_bytes(),entry['mode'])
   else:p.unlink(missing_ok=True)
  run('systemctl','daemon-reload');run('systemctl','reload','caddy.service');run('systemctl','restart','vpn-manager-connect.service')
 try:
  for target,b in data.items():write(Path(target),b)
  write(CADDY,caddynew);run('systemctl','reload','caddy.service')
  active=json.loads(request('http://127.0.0.1:2019/config/'))
  assert comparable(active['apps'])==comparable(adapted['apps']) and 'ticket' in json.dumps(active.get('logging',{}))
  write(ENV,b'[Service]\nEnvironment=CONNECT_CLASH_PILOT=1\n')
  run('systemctl','daemon-reload');run('systemctl','restart','vpn-manager-connect.service')
  admin=Path('/etc/vpn-manager-connect-admin.env').read_text().strip().split('=',1)[1]
  for _ in range(20):
   try:
    status=json.loads(request('http://127.0.0.1:5057/internal/status',headers={'X-Connect-Admin':admin}));break
   except Exception:time.sleep(1)
  else:raise RuntimeError('Portal unavailable')
  def check(cid,pilot):
   issued=json.loads(request('http://127.0.0.1:5057/internal/link',dict(client_id=cid,action='issue'),{'X-Connect-Admin':admin,'Idempotency-Key':'clash-check-'+uuid.uuid4().hex}))
   parts=urlsplit(issued['url']);payload=dict(handle=parts.path[1:],token=parts.fragment)
   client=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
   def post(path,extra=None):return request('https://connect.caesarbot.ru:8444/api/'+path,dict(payload,**(extra or {})),{'Origin':'https://connect.caesarbot.ru:8444'},client)
   login=json.loads(post('login',dict(code=issued['access_code'])))
   assert login['profile']['import_pilot'] is pilot
   if pilot:
    assert post('download')==original
    assert b'<svg' in post('qr')
    uri=json.loads(post('clash-import'))['url'];assert uri.startswith('clashmi://install-config?')
    url=parse_qs(urlsplit(uri).query)['url'][0]
    raw=request(url);lines={k:json.loads(v) for k,v in (s.split(': ',1) for s in raw.decode().splitlines())}
    import configparser
    ini=configparser.ConfigParser(interpolation=None);ini.optionxform=str;ini.read_string(original.decode())
    proxy=lines['proxies'][0]
    assert proxy['private-key']==ini['Interface']['PrivateKey'] and proxy['public-key']==ini['Peer']['PublicKey']
    assert proxy['ip']==ini['Interface']['Address'].split('/')[0]
    post('logout')
    try:request(url);raise AssertionError('Export survived logout')
    except HTTPError as error:assert error.code==404
   else:
    try:post('clash-import');raise AssertionError('Non-pilot import enabled')
    except HTTPError as error:assert error.code==403
    post('logout')
  check(92,True);check(next(cid for cid in status['client_ids'] if cid!=92),False)
  html=request('https://connect.caesarbot.ru:8444/').decode()
  assert html.count('Clash Mi')>=4 and 'defaultvpn' not in html and 'org.amnezia' not in html
  with sqlite3.connect(backup/'clients.db') as old,sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as live:
   for table in ('clients','client_history'):assert old.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()==live.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()
  assert all(digest(Path(p))==d for p,d in profiles.items()) and digest(config)==confighash
  assert run('awg','show','awg-test','peers')==peers
  assert all(run('systemctl','show',name,'-p','MainPID','--value')==pid for name,pid in protected.items())
  print(json.dumps(dict(ok=True,clash_mi_download_links=True,pilot_only=92,export_verified=True,original_config_and_qr_retained=True,clients=len(status['client_ids']),client_data_and_vpn_unchanged=True,real_device_test_pending=True,backup=str(backup))))
 except Exception:
  restore();raise RuntimeError('Portal code and HTTPS configuration restored; current databases and VPN profiles retained') from None
if __name__=='__main__':
 try:main()
 except Exception:raise SystemExit('Installation stopped. No secret details printed. Inspect private backup and preflight.') from None

