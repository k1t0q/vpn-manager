"""Owner-requested daily subscription check; code-only rollback."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,os,subprocess,tempfile,time,json
from urllib.request import urlopen,Request
p=Path('/usr/local/libexec/vpn-manager-production/awg_production_server.py')
def run(*a):return subprocess.run(a,check=True,capture_output=True,text=True,timeout=40).stdout.strip()
def write(b):
 fd,n=tempfile.mkstemp(dir=p.parent,prefix='.daily-')
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o600);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(n,p)
old=p.read_bytes()
assert hashlib.sha1(b'blob '+str(len(old)).encode()+b'\0'+old).hexdigest()=='a33a41a58759ea17ad35abe5fca703aa8d1b4b30','Installed source differs'
new=old.replace(b'next_tick=time.monotonic()+30',b'next_tick=time.monotonic()+86400')
assert new!=old
compile(new,str(p),'exec')
backup=Path('/root/vpn-manager-backups')/('daily-check-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700);(backup/p.name).write_bytes(old);os.chmod(backup/p.name,0o600)
config=Path('/etc/amnezia/amneziawg/awg-test.conf').read_bytes()
peers=run('awg','show','awg-test','peers')
pid=run('systemctl','show','xray.service','-p','MainPID','--value')
run('systemctl','stop','vpn-manager-live-bridge.service')
try:
 write(new);run('systemctl','start','vpn-manager-live-bridge.service')
 env=dict(l.split('=',1) for l in Path('/etc/vpn-manager-live-bridge.env').read_text().splitlines() if '=' in l)
 os.environ['NO_PROXY']='127.0.0.1,localhost'
 for attempt in range(30):
  try:
   with urlopen(Request('http://127.0.0.1:5055/api/managed/health',headers={'X-VPN-Token':env['PANEL_AWG_TOKEN']}),timeout=3) as r:h=json.load(r)
   if h.get('scheduler_ok'):break
  except OSError:pass
  time.sleep(1)
 else:raise RuntimeError('Scheduler health failed')
except Exception:
 run('systemctl','stop','vpn-manager-live-bridge.service');write(old);run('systemctl','start','vpn-manager-live-bridge.service');raise
assert Path('/etc/amnezia/amneziawg/awg-test.conf').read_bytes()==config
assert run('awg','show','awg-test','peers')==peers
assert run('systemctl','show','xray.service','-p','MainPID','--value')==pid
print(json.dumps(dict(ok=True,interval_seconds=86400,scheduler_ok=True,vpn_unchanged=True,backup=str(backup))))
