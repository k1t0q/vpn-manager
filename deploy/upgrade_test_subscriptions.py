"""Owner-run frozen bridge update; original 59 IDs stay protected.

No peer creation, AWG/Xray restart, production DB write or database restore.
Existing staging records, archived clients and history are retained.
"""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,sqlite3,subprocess,tempfile,time
from urllib.request import urlopen,Request

COMMIT='d36223a256200f21ddff87c65ad2fad8ab0d59cd'
OLD={"server/awg_persistence.py":"4cea80527d5bc2bd5668c9e216e023197d85f34e556db4731e1c8cc330474c76","server/awg_provision.py":"0f5caa2ef2180c75cf0e1f581b5a1bee27677a0dcc98c33ff48bacd112ca17fa","server/awg_delete.py":"bf1daaf74d0735c07327d8bfa9d4b949cedd0d28c252057f7804ac456b26a866","server/awg_test_bridge.py":"0e6bdf2472815c623b12e8d6fb16a59a7d61308ac7c338161f68a922ff2eb5c9","deploy/awg_readonly_bridge.py":"96b976a2f7d632c4517a97966d4cc860b2df0f42faba6732f221be9cd98c2f3f","deploy/awg_test_server.py":"0b76fc546abec5ee0d47e3d860462f5d08762f7309da7eb43e13216fd1429707"}
NEW={"server/awg_persistence.py":"0b37f8e7addd4f5fd7162404b3ca98bb7e316b5d55b0ea217dcd54cfd1e7a3c3","server/awg_provision.py":"0f5caa2ef2180c75cf0e1f581b5a1bee27677a0dcc98c33ff48bacd112ca17fa","server/awg_delete.py":"bf1daaf74d0735c07327d8bfa9d4b949cedd0d28c252057f7804ac456b26a866","server/awg_test_bridge.py":"42d1a07d1605c76883781a82861b321d409337c4e523f445b3a6cb83cb030682","deploy/awg_readonly_bridge.py":"96b976a2f7d632c4517a97966d4cc860b2df0f42faba6732f221be9cd98c2f3f","deploy/awg_test_server.py":"49831b9b69d0e182075c14b326014070acbb009e9c3d78143a89ecc1d5116640","server/awg_subscriptions.py":"132b84d3ca5a8660affa80461804d512b1a0bac26ec82d8ca19cbbedb1ea839e"}
CODE=Path('/usr/local/libexec/vpn-manager-awg-stage')
ROOT=Path('/var/lib/vpn-manager-awg-stage')


def write(path,data):
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
  os.replace(name,path)
  fd=os.open(path.parent,os.O_DIRECTORY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if os.path.exists(name):os.unlink(name)


def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 if len(OLD)!=6 or len(NEW)!=7 or set(NEW)-set(OLD)!={'server/awg_subscriptions.py'}:
  raise SystemExit('Invalid upgrade manifest')
 baseline=json.loads((ROOT/'baseline.json').read_text())
 if len(baseline['protected_ids'])!=59 or (ROOT/'operations/pending.json').exists():
  raise SystemExit('Unexpected or pending test state; nothing changed')
 previous={};materials={}
 for source,digest in NEW.items():
  path=CODE/Path(source).name
  if source in OLD:
   if path.is_symlink() or path.stat().st_uid!=0:raise SystemExit('Unexpected installed source owner')
   data=path.read_bytes()
   if hashlib.sha256(data).hexdigest()!=OLD[source]:raise SystemExit('Installed bridge differs; nothing changed')
   previous[path]=data
  elif path.exists():raise SystemExit('New module already exists; inspect before retrying')
  data=urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+COMMIT+'/'+source,timeout=30).read()
  if hashlib.sha256(data).hexdigest()!=digest:raise SystemExit('Downloaded source digest mismatch')
  materials[path]=data
 backup=Path('/root/vpn-manager-backups')/('test-subscriptions-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
 backup.mkdir(parents=True,mode=0o700)
 for path,data in previous.items():write(backup/path.name,data)
 config=Path('/etc/amnezia/amneziawg/awg-test.conf');before=config.read_bytes()
 write(backup/'awg-test.conf',before)
 def peers():return set(subprocess.run(['awg','show','awg-test','peers'],check=True,capture_output=True,timeout=10).stdout.split())
 before_peers=peers()
 try:
  subprocess.run(['systemctl','stop','vpn-manager-awg-readonly.service'],check=True,capture_output=True,timeout=30)
  with sqlite3.connect(backup/'clients.db') as destination:
   with sqlite3.connect((ROOT/'clients.db').as_uri()+'?mode=ro',uri=True) as source:source.backup(destination)
  os.chmod(backup/'clients.db',0o600)
  for path,data in materials.items():write(path,data)
  subprocess.run(['systemctl','start','vpn-manager-awg-readonly.service'],check=True,capture_output=True,timeout=30)
  token=next(line.split('=',1)[1] for line in Path('/etc/vpn-manager-awg-readonly.env').read_text().splitlines() if line.startswith('PANEL_AWG_TOKEN='))
  req=Request('http://127.0.0.1:5054/api/managed/health',headers={'X-VPN-Token':token})
  for attempt in range(8):
   try:
    with urlopen(req,timeout=15) as response:health=json.load(response)
    if health.get('auto_expiry') and health.get('scheduler_ok'):break
   except OSError:
    if attempt==7:raise
   time.sleep(1)
  else:raise RuntimeError('Scheduler health not confirmed')
  if config.read_bytes()!=before or peers()!=before_peers:raise RuntimeError('Installation invariant failed')
 except Exception:
  for path,data in previous.items():write(path,data)
  for path in materials:
   if path not in previous and path.exists():os.replace(path,backup/('failed-'+path.name))
  subprocess.run(['systemctl','restart','vpn-manager-awg-readonly.service'],capture_output=True,timeout=30)
  raise SystemExit('Update failed; previous bridge code restored. Inspect '+str(backup)) from None
 print('TEST_SUBSCRIPTIONS_READY; protected_clients=59; profiles_created=0; VPN unchanged')
 print('Notifications: panel outbox only; external delivery not configured')
 print('Rollback code files: '+str(backup)+'; never replace a newer client database during code rollback')


if __name__=='__main__':main()
