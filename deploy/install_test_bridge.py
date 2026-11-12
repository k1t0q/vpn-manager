"""Explicit owner-run installation of the frozen NEW-test-peer management bridge.

Does not create peers, change the AWG config, or restart AWG/Xray. Existing
59 client IDs are permanently protected in this staging bridge installation.
"""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,shutil,sqlite3,subprocess,sys,time
from urllib.request import urlopen,Request

COMMIT='7c8d603cca503641dc2ddad08f9d0c341bc6161f'
FILES={"server/awg_persistence.py":"0b37f8e7addd4f5fd7162404b3ca98bb7e316b5d55b0ea217dcd54cfd1e7a3c3","server/awg_provision.py":"0f5caa2ef2180c75cf0e1f581b5a1bee27677a0dcc98c33ff48bacd112ca17fa","server/awg_delete.py":"bf1daaf74d0735c07327d8bfa9d4b949cedd0d28c252057f7804ac456b26a866","server/awg_test_bridge.py":"42d1a07d1605c76883781a82861b321d409337c4e523f445b3a6cb83cb030682","deploy/awg_readonly_bridge.py":"96b976a2f7d632c4517a97966d4cc860b2df0f42faba6732f221be9cd98c2f3f","deploy/awg_test_server.py":"49831b9b69d0e182075c14b326014070acbb009e9c3d78143a89ecc1d5116640","server/awg_subscriptions.py":"134dee55fe6d581d837b4b52c03f934102283455fde21ec7933b4061bbb505aa"}
ROOT=Path('/var/lib/vpn-manager-awg-stage')


def write(path,data,mode=0o600):
 import tempfile
 path=Path(path);fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
 try:
  os.fchmod(fd,mode)
  with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
  os.replace(name,path)
  descriptor=os.open(path.parent,os.O_DIRECTORY)
  try:os.fsync(descriptor)
  finally:os.close(descriptor)
 finally:
  if os.path.exists(name):os.unlink(name)


def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 if ROOT.exists():raise SystemExit('Managed staging already prepared; inspect before retrying')
 env=Path('/etc/vpn-manager-stage.env');before_env=env.read_bytes()
 if b'PANEL_MANAGED_AWG=test-only' in before_env:raise SystemExit('Management already enabled')
 unit=Path('/etc/systemd/system/vpn-manager-awg-readonly.service');before_unit=unit.read_bytes()
 token_env=Path('/etc/vpn-manager-awg-readonly.env')
 if not token_env.exists():raise SystemExit('Install and verify the read-only bridge first')
 if not shutil.which('qrencode'):raise SystemExit('qrencode is required; no server settings changed')
 materials={}
 for path,digest in FILES.items():
  data=urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+COMMIT+'/'+path,timeout=30).read()
  if hashlib.sha256(data).hexdigest()!=digest:raise SystemExit('Source verification failed; no server settings changed')
  materials[path]=data
 if len(materials)!=7:raise SystemExit('Incomplete source manifest')
 code_dir=Path('/usr/local/libexec/vpn-manager-awg-stage')
 code_dir.mkdir(mode=0o700)
 for path,data in materials.items():write(code_dir/Path(path).name,data)
 sys.path.insert(0,str(code_dir))
 from awg_persistence import AWGRuntime,peer_blocks
 config=Path('/etc/amnezia/amneziawg/awg-test.conf');before_config=config.read_bytes()
 header,blocks=peer_blocks(before_config.decode())
 import re
 if re.search(r'(?im)^\s*SaveConfig\s*=\s*true',header):raise SystemExit('SaveConfig must be reviewed before enabling test management')
 peers={key:fields for key,fields,_ in blocks}
 runtime=AWGRuntime();live=runtime.peers()
 database=Path('/opt/vpn-manager/data/clients-staging.db')
 with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as con:
  rows=con.execute('SELECT id FROM clients ORDER BY id').fetchall()
 if len(rows)!=59:raise SystemExit('Expected exactly 59 existing clients; no server settings changed')
 profiles=Path('/opt/vpn-manager/awg-clients');metas={}
 for (cid,) in rows:
  folder=profiles/str(cid)
  if folder.is_symlink():raise SystemExit('Profile symlink requires review')
  meta=json.loads((folder/'metadata.json').read_text());public=meta['public_key']
  if str(meta['client_id'])!=str(cid) or public not in live or peers.get(public,{}).get('AllowedIPs')!=meta['address']+'/32':
   raise SystemExit('Existing profile consistency check failed; nothing activated')
  metas[cid]=meta
 if len({meta['public_key'] for meta in metas.values()})!=59:
  raise SystemExit('Duplicate existing profile mapping')
 backup=Path('/root/vpn-manager-backups')/('test-bridge-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
 backup.mkdir(mode=0o700,parents=True)
 write(backup/'stage.env',before_env);write(backup/'bridge.service',before_unit);write(backup/'awg-test.conf',before_config)
 ROOT.mkdir(mode=0o700);(ROOT/'profiles').mkdir(mode=0o700)
 for cid,meta in metas.items():
  (ROOT/'profiles'/str(cid)).mkdir(mode=0o700)
  write(ROOT/'profiles'/str(cid)/'metadata.json',json.dumps(meta).encode())
 write(ROOT/'template.conf',(profiles/str(rows[0][0])/'client.conf').read_bytes())
 with sqlite3.connect(ROOT/'clients.db') as destination:
  with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as source:source.backup(destination)
 os.chmod(ROOT/'clients.db',0o600)
 write(ROOT/'baseline.json',json.dumps(dict(protected_ids=[row[0] for row in rows],peers=peers,runtime_peers=list(live))).encode())
 # One HTTP request at a time; engines additionally serialize all mutations via flock.
 new_unit=before_unit.decode().replace('local read-only AWG status','protected staging test peer management').replace('/usr/local/libexec/vpn-manager-awg-readonly.py',str(code_dir/'awg_test_server.py'))
 new_unit=new_unit.replace('ProtectSystem=strict','ProtectSystem=strict\nReadWritePaths=/var/lib/vpn-manager-awg-stage /etc/amnezia/amneziawg')
 try:
  write(unit,new_unit.encode(),0o644)
  write(env,before_env+b'\nPANEL_MANAGED_AWG=test-only\n')
  for args in [['systemctl','daemon-reload'],['systemctl','restart','vpn-manager-awg-readonly.service'],['systemctl','restart','vpn-manager-stage.service']]:
   subprocess.run(args,check=True,capture_output=True,timeout=30)
  token=next(line.split('=',1)[1] for line in token_env.read_text().splitlines() if line.startswith('PANEL_AWG_TOKEN='))
  req=Request('http://127.0.0.1:5054/api/managed/clients',headers={'X-VPN-Token':token})
  for attempt in range(5):
   try:
    with urlopen(req,timeout=15) as response:count=len(json.load(response)['clients'])
    break
   except OSError:
    if attempt==4:raise
    time.sleep(1)
  if count!=59 or config.read_bytes()!=before_config or runtime.peers()!=live:
   raise RuntimeError('Installation invariant failed')
 except Exception:
  write(env,before_env);write(unit,before_unit,0o644)
  for args in [['systemctl','daemon-reload'],['systemctl','restart','vpn-manager-awg-readonly.service'],['systemctl','restart','vpn-manager-stage.service']]:subprocess.run(args,capture_output=True)
  raise SystemExit('Bridge activation failed; preview connection restored. Inspect '+str(backup)) from None
 print('TEST_BRIDGE_READY; protected_clients=59; peers_created=0; VPN unchanged')
 print('Rollback: restore stage.env and bridge.service from '+str(backup)+'; reload systemd and restart only the two preview services')


if __name__=='__main__':main()
