"""Pinned compatibility fix for the private test bridge, never working client data."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,sqlite3,subprocess,tempfile,time,sys
from urllib.request import urlopen,Request

COMMIT='a1e3afa51c0cb0114cde943ba3952ebb6014f7fb'
OLD={"server/awg_provision.py":"a5afc89bd7951e6099edfd024055f1670d864e15cdcd7190d58f6f41610a76b9","deploy/awg_test_server.py":"0b1cfff6349a6814c363181dc105b32c046fb9541c7c2f2fbeb6b3e46d29dc0c"}
NEW={"server/awg_provision.py":"0f5caa2ef2180c75cf0e1f581b5a1bee27677a0dcc98c33ff48bacd112ca17fa","deploy/awg_test_server.py":"0b76fc546abec5ee0d47e3d860462f5d08762f7309da7eb43e13216fd1429707"}
CODE=Path('/usr/local/libexec/vpn-manager-awg-stage')
ROOT=Path('/var/lib/vpn-manager-awg-stage')


def write(path,data):
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
  os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)


def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 if len(OLD)!=2 or set(NEW)!=set(OLD):raise SystemExit('Invalid compatibility manifest')
 baseline=json.loads((ROOT/'baseline.json').read_text())
 if len(baseline['protected_ids'])!=59 or (ROOT/'operations/pending.json').exists():
  raise SystemExit('Unexpected or pending test state; nothing changed')
 with sqlite3.connect((ROOT/'clients.db').as_uri()+'?mode=ro',uri=True) as con:
  count=con.execute('SELECT count(*) FROM clients').fetchone()[0]
 if count!=59:raise SystemExit('Test data already changed; inspect before upgrading')
 materials={};previous={}
 for source,digest in NEW.items():
  path=CODE/Path(source).name
  if path.is_symlink() or path.stat().st_uid!=0:raise SystemExit('Unexpected installed source owner')
  before=path.read_bytes()
  if hashlib.sha256(before).hexdigest()!=OLD[source]:raise SystemExit('Installed version differs; nothing changed')
  data=urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+COMMIT+'/'+source,timeout=30).read()
  if hashlib.sha256(data).hexdigest()!=digest:raise SystemExit('Downloaded source digest mismatch')
  previous[path]=before;materials[path]=data
 backup=Path('/root/vpn-manager-backups')/('test-owner-fix-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
 backup.mkdir(parents=True,mode=0o700)
 for path,data in previous.items():write(backup/path.name,data)
 config=Path('/etc/amnezia/amneziawg/awg-test.conf');config_hash=hashlib.sha256(config.read_bytes()).digest()
 def peers():return subprocess.run(['awg','show','awg-test','peers'],check=True,capture_output=True,timeout=10).stdout
 before_peers=peers()
 try:
  subprocess.run(['systemctl','stop','vpn-manager-awg-readonly.service'],check=True,capture_output=True,timeout=30)
  for path,data in materials.items():write(path,data)
  # Exercise actual DB ownership, address allocation and profile-template
  # validation, stopping BEFORE any key generation or peer/config write.
  sys.path.insert(0,str(CODE))
  from awg_provision import Provisioner
  from awg_persistence import AWGRuntime
  class PreflightComplete(Exception):pass
  class PreflightRuntime(AWGRuntime):
   def new_keys(self):raise PreflightComplete()
  protected=set(map(str,baseline['protected_ids']))
  store=Provisioner(ROOT/'clients.db',config,ROOT/'profiles',ROOT/'operations',PreflightRuntime(),protected_ids=protected)
  with store.connect() as con:
   owner_rows=con.execute('SELECT id,user_id FROM clients WHERE user_id IS NOT NULL ORDER BY id').fetchall()
  store.owner_id=next((row['user_id'] for row in owner_rows if str(row['id']) in protected and str(row['user_id']).strip()),None)
  try:
   store.create(dict(name='STAGING TEST Preflight',phone='',note='',period='1m',start_date=datetime.now(timezone.utc).date().isoformat()),'owner-fix-preflight-01',ROOT/'template.conf')
   raise RuntimeError('Preflight unexpectedly returned')
  except PreflightComplete:pass
  with store.connect() as con:
   con.execute('BEGIN IMMEDIATE')
   try:
    cid=con.execute('SELECT max(id)+1 FROM clients').fetchone()[0]
    con.execute('INSERT INTO clients(id,user_id,name,phone,start_date,end_date,period,note,referrer_id,wg_client_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (cid,store.owner_id,'STAGING TEST SQL preflight','','2026-11-12','2026-12-12','1m','',None,str(cid)))
    con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',
                (store.owner_id,cid,'created','Rolled back compatibility preflight'))
   finally:con.rollback()
  subprocess.run(['systemctl','start','vpn-manager-awg-readonly.service'],check=True,capture_output=True,timeout=30)
  token=next(line.split('=',1)[1] for line in Path('/etc/vpn-manager-awg-readonly.env').read_text().splitlines() if line.startswith('PANEL_AWG_TOKEN='))
  req=Request('http://127.0.0.1:5054/api/managed/clients',headers={'X-VPN-Token':token})
  for attempt in range(5):
   try:
    with urlopen(req,timeout=15) as response:loaded=len(json.load(response)['clients'])
    break
   except OSError:
    if attempt==4:raise
    time.sleep(1)
  if loaded!=59 or hashlib.sha256(config.read_bytes()).digest()!=config_hash or set(peers().split())!=set(before_peers.split()):
   raise RuntimeError('Test update invariant failed')
 except Exception:
  for path,data in previous.items():write(path,data)
  subprocess.run(['systemctl','restart','vpn-manager-awg-readonly.service'],capture_output=True,timeout=30)
  raise SystemExit('Compatibility fix failed; previous test bridge restored. Backup: '+str(backup)) from None
 print('TEST_OWNER_FIX_READY; protected_clients=59; profiles_created=0; VPN unchanged')
 print('Rollback files: '+str(backup))


if __name__=='__main__':main()
