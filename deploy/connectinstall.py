"""Owner-approved one-client pilot. Never restart AWG/Xray or restore old client DB."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib, json, os, pwd, secrets, shutil, sqlite3, subprocess, sys, tempfile, time, uuid
from urllib.request import Request, urlopen

RELEASE='5ee7c8e07d1fbad17d77a17bec8e527336a5d200'
SITE=Path('/opt/vpn-manager-production'); CODE=Path('/opt/vpn-manager-connect')
STATE=Path('/var/lib/vpn-manager-connect'); LEDGER=Path('/root/vpn-connect-pilot-install.json')
CONFIG=Path('/etc/amnezia/amneziawg/awg-test.conf'); PROFILES=Path('/opt/vpn-manager/awg-clients')
CADDY=Path('/etc/caddy/Caddyfile'); SNIPPET=Path('/etc/caddy/vpn-manager-connect.caddy')
PYTHON=SITE/'venv/bin/python'; ADMIN_ENV=Path('/etc/vpn-manager-connect-admin.env')
SNAPSHOT=Path('/usr/local/libexec/vpn-manager-connect-snapshot.py')
FILES=[
 ('server/connect_service.py',CODE/'connect_service.py',None,'5d1f82a9badfbff9a94394255450ff0be7eb7845'),
 ('server/connect.html',CODE/'connect.html',None,'e45c680a8c41b16dc03a37db852a944e2fff0b51'),
 ('server/connect.js',CODE/'connect.js',None,'f26f0a23dc3879ebe3bb01eaf7896131134c802c'),
 ('server/connect.css',CODE/'connect.css',None,'e49e68d4297895906a0508f9586401f8caa88a0f'),
 ('deploy/connectsnapshot.py',SNAPSHOT,None,'7441602b5fc4ac6c29b0163d42bee3147c80b179'),
 ('server/connect_admin.py',SITE/'server/connect_admin.py',None,'8ae09a9811bdad6f3ad452f3374da75a2ef483ca'),
 ('server/production_app.py',SITE/'server/production_app.py','0543ad2fa1abf6c36c3f1ac865f75cc51cad5fad','d7e9a0fef8884bfab8e0ebe6a74b734974e31b33'),
 ('server/staging_api.py',SITE/'server/staging_api.py','a0860b04bed4d5b71402d643e0f8104a1e4c5cd3','78534adad1f9d0728de488decfb967b51c631a02'),
 ('sqlite-manager.js',SITE/'public/sqlite-manager.js','82da9f6f2a9266624dd31d084e7b2df2da0d5bb6','307b05babf42d20329510b8a10d9e3f6bf26f690'),
 ('index.html',SITE/'public/index.html','35bb2594dc299319c72ea211476e874f51584c45','86270637f03393c9486ab532cfd4a691795907ac')]


def run(*args,timeout=40):
 return subprocess.run([str(a) for a in args],check=True,capture_output=True,text=True,timeout=timeout).stdout.strip()


def write(path,data,mode=0o600,uid=0,gid=0):
 path.parent.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.connect-')
 with os.fdopen(fd,'wb') as out:
  os.fchmod(out.fileno(),mode);os.fchown(out.fileno(),uid,gid)
  out.write(data);out.flush();os.fsync(out.fileno())
 os.replace(name,path)


def blob(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(state):write(LEDGER,json.dumps(state).encode())
def adapt(path):return json.loads(run('caddy','adapt','--config',path,'--adapter','caddyfile'))
def listeners(config):return Counter(addr for item in config['apps']['http']['servers'].values() for addr in item.get('listen',[]))


def request(url,data=None,headers=None):
 headers=dict(headers or {})
 if data is not None:headers['Content-Type']='application/json'
 req=Request(url,data=None if data is None else json.dumps(data).encode(),headers=headers)
 with urlopen(req,timeout=20) as response:return response.read()


def backup_db(source,target):
 with sqlite3.connect(target) as dest:
  with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as con:con.backup(dest)
  assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def rollback(state):
 backup=Path(state['backup'])
 for name in ['vpn-manager-connect.timer','vpn-manager-connect-snapshot.timer']:
  subprocess.run(['systemctl','disable','--now',name],capture_output=True)
 for name in ['vpn-manager-connect.service','vpn-manager-connect-snapshot.service']:
  subprocess.run(['systemctl','stop',name],capture_output=True)
 for item in json.loads((backup/'files.json').read_text()):
  if item['existed']:write(Path(item['path']),(backup/item['saved']).read_bytes(),item['mode'])
 if sha(CADDY) not in {state['caddy_original'],state.get('caddy_installed')}:
  raise SystemExit('Caddy changed independently; inspect before routing rollback')
 write(CADDY,(backup/'Caddyfile').read_bytes(),state['caddy_mode'])
 run('caddy','reload','--config',CADDY,'--adapter','caddyfile')
 run('systemctl','restart','vpn-manager-live-api.service')
 state['phase']='rolled-back';save(state)
 print('CODE_ROUTING_ROLLBACK_READY; current client databases and pilot profile retained')


def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 os.umask(0o077);os.environ['NO_PROXY']='127.0.0.1,localhost'
 state=json.loads(LEDGER.read_text()) if LEDGER.exists() else None
 if '--rollback' in sys.argv:
  if not state:raise SystemExit('No installation ledger')
  return rollback(state)
 if state and state.get('phase')=='complete':print(json.dumps(state['report'],indent=2));return
 if Path('/var/lib/vpn-manager-production/operations/pending.json').exists():raise SystemExit('Pending VPN operation; review first')
 materials={}
 for source,target,old,new in FILES:
  if target.is_symlink():raise SystemExit('Symlink target rejected')
  if target.exists():
   if blob(target.read_bytes()) not in {old,new}:raise SystemExit('Installed code differs: '+source)
  elif old is not None:raise SystemExit('Existing source missing: '+source)
  with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source,timeout=30) as response:data=response.read()
  if blob(data)!=new:raise SystemExit('Source digest mismatch')
  if source.endswith('.py'):compile(data,source,'exec')
  materials[source]=data
 if state is None:
  if SNIPPET.exists() or ADMIN_ENV.exists() or STATE.exists():raise SystemExit('Unrecognized pilot preparation; inspect first')
  audit=json.loads(Path('/root/vpn-connect-audit.json').read_text())
  if sha(CADDY)!=audit['files'][str(CADDY)]:raise SystemExit('Caddy differs from audited source')
  backup=Path('/root/vpn-manager-backups')/('connect-pilot-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700)
  manifest=[]
  for index,(source,target,old,new) in enumerate(FILES):
   item=dict(path=str(target),existed=target.exists(),saved=str(index),mode=target.stat().st_mode & 0o777 if target.exists() else 0o644)
   if target.exists():shutil.copy2(target,backup/str(index))
   manifest.append(item)
  (backup/'files.json').write_text(json.dumps(manifest));shutil.copy2(CADDY,backup/'Caddyfile')
  backup_db(Path('/opt/vpn-manager/data/clients-staging.db'),backup/'clients.db')
  shutil.copytree(PROFILES,backup/'profiles');(backup/'awg-test.conf').write_bytes(CONFIG.read_bytes())
  state=dict(backup=str(backup),phase='prepared',caddy_original=sha(CADDY),caddy_mode=CADDY.stat().st_mode & 0o777,
             original_config=sha(CONFIG),original_peers=run('awg','show','awg-test','peers').split(),
             profile_hashes={str(p):sha(p) for p in PROFILES.glob('*/client.conf')},
             xray_pid=run('systemctl','show','xray.service','-p','MainPID','--value'),bridge_pid=run('systemctl','show','vpn-manager-live-bridge.service','-p','MainPID','--value'),
             request_id='connect-pilot-'+uuid.uuid4().hex,
             client_data=dict(name='STAGING TEST DefaultVPN pilot',phone='',note='Isolated owner-approved DefaultVPN file-import pilot',period='1m',start_date=datetime.now(timezone(timedelta(hours=3))).date().isoformat(),referrer_id=None))
  save(state)
 backup=Path(state['backup'])
 try:
  try:account=pwd.getpwnam('vpn-connect')
  except KeyError:
   run('useradd','--system','--user-group','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','vpn-connect');account=pwd.getpwnam('vpn-connect')
  CODE.mkdir(exist_ok=True);CODE.chmod(0o755)
  STATE.mkdir(exist_ok=True);STATE.chmod(0o755)
  (STATE/'state').mkdir(exist_ok=True);(STATE/'state').chmod(0o700);os.chown(STATE/'state',account.pw_uid,account.pw_gid)
  (STATE/'material').mkdir(exist_ok=True);(STATE/'material').chmod(0o750);os.chown(STATE/'material',0,account.pw_gid)
  if not ADMIN_ENV.exists():write(ADMIN_ENV,('CONNECT_ADMIN_TOKEN='+secrets.token_urlsafe(32)+'\n').encode())
  admin_token=ADMIN_ENV.read_text().strip().split('=',1)[1]
  for source,target,old,new in FILES:
   if target not in [SITE/'server/production_app.py',SITE/'server/staging_api.py',SITE/'public/index.html',SITE/'public/sqlite-manager.js']:
    write(target,materials[source],0o600 if target==SNAPSHOT else 0o644)
  service='''[Unit]
Description=Isolated DefaultVPN profile link pilot
After=network.target
[Service]
User=vpn-connect
WorkingDirectory=/opt/vpn-manager-connect
EnvironmentFile=/etc/vpn-manager-connect-admin.env
Environment=CONNECT_RUN=1
ExecStart=/opt/vpn-manager-production/venv/bin/python -m gunicorn --workers 1 --bind 127.0.0.1:5057 connect_service:app
Restart=on-failure
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
CapabilityBoundingSet=
ReadWritePaths=/var/lib/vpn-manager-connect/state
InaccessiblePaths=-/etc/amnezia -/opt/vpn-manager -/opt/vpn-manager-production/server -/var/lib/vpn-manager-production -/var/lib/vpn-manager-production-auth -/usr/local/libexec/vpn-manager-production -/etc/vpn-manager-live.env -/etc/vpn-manager-live-bridge.env
[Install]
WantedBy=multi-user.target
'''
  snapshot_service='''[Unit]
Description=Read-only single pilot profile snapshot
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/libexec/vpn-manager-connect-snapshot.py
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
CapabilityBoundingSet=CAP_CHOWN
ReadWritePaths=/var/lib/vpn-manager-connect/material
InaccessiblePaths=-/etc/amnezia -/var/lib/vpn-manager-production -/etc/vpn-manager-live.env -/etc/vpn-manager-live-bridge.env
'''
  timer='''[Unit]
Description=Refresh single pilot availability
[Timer]
OnBootSec=20s
OnUnitActiveSec=60s
Unit=vpn-manager-connect-snapshot.service
[Install]
WantedBy=timers.target
'''
  for name,text in [('vpn-manager-connect.service',service),('vpn-manager-connect-snapshot.service',snapshot_service),('vpn-manager-connect-snapshot.timer',timer)]:write(Path('/etc/systemd/system')/name,text.encode(),0o644)
  run('systemctl','daemon-reload');run('systemctl','enable','--now','vpn-manager-connect.service')
  for _ in range(20):
   try:
    assert b'CAESAR BOT' in request('http://127.0.0.1:5057/');break
   except Exception:time.sleep(1)
  else:raise RuntimeError('Public pilot backend unavailable')
  state['phase']='https';save(state)
  snippet='''https://connect.caesarbot.ru:8444 {
    tls {
        issuer acme {
            dir https://acme-v02.api.letsencrypt.org/directory
            disable_tlsalpn_challenge
        }
    }
    @internal path /internal/*
    respond @internal 404
    reverse_proxy 127.0.0.1:5057 {
        header_up X-Connect-IP {remote_host}
    }
}
'''
  write(SNIPPET,snippet.encode(),0o644)
  original=(backup/'Caddyfile').read_bytes();before=adapt(backup/'Caddyfile')
  candidate=original.rstrip()+b'\nimport /etc/caddy/vpn-manager-connect.caddy\n'
  trial=CADDY.parent/'.connect-candidate';write(trial,candidate,state['caddy_mode'])
  after=adapt(trial)
  assert not (listeners(before)-listeners(after))
  assert all(address.endswith((':80',':8444')) for address in (listeners(after)-listeners(before)))
  def canonical(route):
   names={}
   def walk(value):
    if isinstance(value,dict):return {key:(names.setdefault(item,'group'+str(len(names))) if key=='group' and isinstance(item,str) else walk(item)) for key,item in value.items()}
    if isinstance(value,list):return [walk(item) for item in value]
    return value
   return json.dumps(walk(route),sort_keys=True)
  old_routes=[canonical(route) for server in before['apps']['http']['servers'].values() for route in server.get('routes',[])]
  new_routes=[canonical(route) for server in after['apps']['http']['servers'].values() for route in server.get('routes',[])]
  assert all(route in new_routes for route in old_routes)
  run('caddy','validate','--config',trial,'--adapter','caddyfile')
  if sha(CADDY) not in {state['caddy_original'],state.get('caddy_installed')}:raise RuntimeError('Concurrent Caddy change')
  write(CADDY,candidate,state['caddy_mode']);state['caddy_installed']=sha(CADDY);save(state)
  run('caddy','reload','--config',CADDY,'--adapter','caddyfile')
  for _ in range(60):
   try:
    assert b'CAESAR BOT' in request('https://connect.caesarbot.ru:8444/');break
   except Exception:time.sleep(2)
  else:raise RuntimeError('Trusted HTTPS certificate unavailable')
  assert b'v1.76' in request('https://185-23-239-142.sslip.io:8444/panel-live/') or state.get('client_id')
  state['phase']='provision-test';save(state)
  env=dict(line.split('=',1) for line in Path('/etc/vpn-manager-live.env').read_text().splitlines() if '=' in line)
  login=json.loads(request('http://127.0.0.1:5056/auth/login',dict(code=env['PANEL_ADMIN_CODE'])))
  headers={'Authorization':'Bearer '+login['access_token']}
  if not state.get('client_id'):
   result=json.loads(request('http://127.0.0.1:5056/api/clients/provision',state['client_data'],dict(headers,**{'Idempotency-Key':state['request_id']})))
   state['client_id']=int(result['client_id']);save(state)
  cid=state['client_id'];write(Path('/etc/vpn-manager-connect-pilot.json'),json.dumps(dict(client_id=cid)).encode())
  run('systemctl','start','vpn-manager-connect-snapshot.service')
  run('systemctl','enable','--now','vpn-manager-connect-snapshot.timer')
  # Register only admin routes in the existing unprivileged API. The root
  # VPN bridge and its frozen files remain untouched.
  state['phase']='admin-api';save(state)
  for source,target,old,new in FILES:
   if target in [SITE/'server/production_app.py',SITE/'server/staging_api.py',SITE/'public/index.html',SITE/'public/sqlite-manager.js']:write(target,materials[source],0o644)
  write(Path('/etc/systemd/system/vpn-manager-live-api.service.d/30-connect-pilot.conf'),b'[Service]\nEnvironmentFile=/etc/vpn-manager-connect-admin.env\n',0o644)
  run('systemctl','daemon-reload');run('systemctl','restart','vpn-manager-live-api.service')
  for _ in range(20):
   try:
    info=json.loads(request('http://127.0.0.1:5056/api/connect/pilot',headers=headers))
    if info.get('client_id')==cid:break
   except Exception:time.sleep(1)
  else:raise RuntimeError('Pilot admin API unavailable')
  state['phase']='verify';save(state)
  def link(action,key):return json.loads(request('http://127.0.0.1:5056/api/connect/clients/'+str(cid)+'/link',dict(action=action),dict(headers,**{'Idempotency-Key':key})))
  from urllib.parse import urlsplit
  verify_id='pilot-check-'+uuid.uuid4().hex
  first=link('issue',verify_id+'-issue')
  def download(url):
   parts=urlsplit(url)
   return request('https://connect.caesarbot.ru:8444/api/download',dict(handle=parts.path[1:],token=parts.fragment),{'Origin':'https://connect.caesarbot.ru:8444'})
  assert download(first['url'])==(PROFILES/str(cid)/'client.conf').read_bytes()
  link('revoke',verify_id+'-revoke')
  from urllib.error import HTTPError
  try:download(first['url'])
  except HTTPError as error:assert error.code==404
  else:raise RuntimeError('Revoked link accepted')
  second=link('rotate',verify_id+'-rotate')
  assert second['url']!=first['url'];assert download(second['url'])==(PROFILES/str(cid)/'client.conf').read_bytes()
  run('systemctl','restart','vpn-manager-connect.service')
  for _ in range(20):
   try:
    assert link('issue',verify_id+'-stable')['url']==second['url'];break
   except Exception:time.sleep(1)
  else:raise RuntimeError('Stable link after restart not verified')
  qr=request('http://127.0.0.1:5056/api/wg/client/'+str(cid)+'/qr',headers=headers)
  assert b'<svg' in qr
  # Verify every original profile and peer, allowing exactly the newly created test peer.
  sys.path.insert(0,'/usr/local/libexec/vpn-manager-production')
  from awg_persistence import peer_blocks
  old_header,old_blocks=peer_blocks((backup/'awg-test.conf').read_text());new_header,new_blocks=peer_blocks(CONFIG.read_text())
  old={key:fields for key,fields,_ in old_blocks};new={key:fields for key,fields,_ in new_blocks}
  assert old_header==new_header and all(new.get(key)==fields for key,fields in old.items())
  meta=json.loads((PROFILES/str(cid)/'metadata.json').read_text());pilot_key=meta['public_key']
  assert set(new)-set(old)=={pilot_key}
  assert set(run('awg','show','awg-test','peers').split())==set(state['original_peers'])|{pilot_key}
  assert all(Path(path).exists() and sha(Path(path))==digest for path,digest in state['profile_hashes'].items())
  assert run('systemctl','show','xray.service','-p','MainPID','--value')==state['xray_pid']
  assert run('systemctl','show','vpn-manager-live-bridge.service','-p','MainPID','--value')==state['bridge_pid']
  with sqlite3.connect(backup/'clients.db') as old_db, sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True) as live_db:
   old_rows=old_db.execute('SELECT * FROM clients ORDER BY id').fetchall()
   new_rows=live_db.execute('SELECT * FROM clients WHERE id!=? ORDER BY id',(cid,)).fetchall()
   assert old_rows==new_rows
   old_history=old_db.execute('SELECT * FROM client_history ORDER BY id').fetchall()
   live_history=live_db.execute('SELECT * FROM client_history WHERE client_id!=? ORDER BY id',(cid,)).fetchall()
   assert old_history==live_history
   assert live_db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
  request('http://127.0.0.1:5056/auth/logout',{},headers)
  assert b'v1.77' in request('https://185-23-239-142.sslip.io:8444/panel-live/')
  backup_db(STATE/'state/links.db',backup/'links.db');shutil.copy2(STATE/'state/link.key',backup/'link.key')
  report=dict(ok=True,version='1.77',pilot_client_id=cid,client_name=state['client_data']['name'],page='https://connect.caesarbot.ru:8444/',https_verified=True,download_bytes_match=True,revoke_rotate_verified=True,stable_after_restart=True,original_profiles_unchanged=True,xray_and_awg_not_restarted=True,backup=str(backup),iphone_import_pending=True)
  state['phase']='complete';state['report']=report;save(state)
  print(json.dumps(report,indent=2))
 except Exception as error:
  phase=state['phase'];rollback(state)
  print(json.dumps(dict(ok=False,failed_phase=phase,error_type=type(error).__name__,backup=state['backup'],pilot_client_id=state.get('client_id'))))
  raise SystemExit('Pilot installation rolled back; current client DB and any new test profile retained') from None


if __name__=='__main__':main()
