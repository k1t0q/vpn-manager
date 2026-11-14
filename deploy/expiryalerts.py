"""Install expiry warning milestones. Never restart AWG/Xray or restore client data."""
from datetime import datetime,timezone
import hashlib,json,os,shutil,sqlite3,subprocess,tempfile,time
from pathlib import Path
from urllib.request import Request,urlopen
FILES=[["server/awg_subscriptions.py","/usr/local/libexec/vpn-manager-production/awg_subscriptions.py","ea7cecde6a1132fd8f25a7e3f6b9ef68171893a3","8a5f8d65eea38cf645ca3f1854d94a2bc1981213",384],["sqlite-manager.js","/opt/vpn-manager-production/public/sqlite-manager.js","5de60a45dc97a7bcda589a5492818e00f5558eff","7738a0cf733bd34078852a89d046c5eadaa91bac",420],["index.html","/opt/vpn-manager-production/public/index.html","90e0ad4cfc3fc5550ab19b248ed05cdfec72ccf1","89d8bf7bdf6d28bf20197b91aca7fd283bbf1d4e",420]]
ROOT=Path('/var/lib/vpn-manager-production')
DB=Path('/opt/vpn-manager/data/clients-staging.db')
CONFIG=Path('/etc/amnezia/amneziawg/awg-test.conf')
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=40).stdout.strip()
def blob(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def write(path,data,mode):
 fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.expiry-alerts-')
 with os.fdopen(fd,'wb') as out:os.fchmod(out.fileno(),mode);out.write(data);out.flush();os.fsync(out.fileno())
 os.replace(tmp,path)
def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 os.umask(0o077);os.environ['NO_PROXY']='127.0.0.1,localhost';materials={}
 if (ROOT/'operations/pending.json').exists():raise SystemExit('Pending operation; inspect before update')
 if not json.loads((ROOT/'activation.json').read_text()).get('approved'):raise SystemExit('No approved production activation')
 for source,target,old,new,mode in FILES:
  path=Path(target)
  if path.is_symlink() or blob(path.read_bytes())!=old:raise SystemExit('Installed source differs; review before update')
  with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/2fbbaa1ac773dbca35356d69f0b0d08d119bdb3e/'+source,timeout=30) as response:data=response.read()
  if blob(data)!=new:raise SystemExit('New source digest mismatch')
  if source.endswith('.py'):compile(data,source,'exec')
  materials[source]=data
 config_before=CONFIG.read_bytes();peers_before=set(run('awg','show','awg-test','peers').split())
 xray=run('systemctl','show','xray.service','-p','MainPID','--value')
 backup=Path('/root/vpn-manager-backups')/('expiry-alerts-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
 backup.mkdir(mode=0o700)
 for source,target,old,new,mode in FILES:shutil.copy2(target,backup/Path(target).name)
 (backup/'awg-test.conf').write_bytes(config_before)
 run('systemctl','stop','vpn-manager-live-bridge.service')
 try:
  with sqlite3.connect(backup/'clients.db') as target:
   with sqlite3.connect(DB) as source:source.backup(target)
  for source,target,old,new,mode in FILES:write(Path(target),materials[source],mode)
  run('systemctl','start','vpn-manager-live-bridge.service')
  env=dict(line.split('=',1) for line in Path('/etc/vpn-manager-live-bridge.env').read_text().splitlines() if '=' in line)
  for attempt in range(30):
   try:
    request=Request('http://127.0.0.1:5055/api/managed/health',headers={'X-VPN-Token':env['PANEL_AWG_TOKEN']})
    with urlopen(request,timeout=3) as response:health=json.load(response)
    if health.get('scheduler_ok'):break
   except OSError:pass
   time.sleep(1)
  else:raise RuntimeError('New scheduler health not verified')
 except Exception:
  run('systemctl','stop','vpn-manager-live-bridge.service')
  for source,target,old,new,mode in FILES:write(Path(target),(backup/Path(target).name).read_bytes(),mode)
  run('systemctl','start','vpn-manager-live-bridge.service')
  raise SystemExit('Previous code restored; current database and VPN state preserved; inspect backup')
 assert CONFIG.read_bytes()==config_before
 assert set(run('awg','show','awg-test','peers').split())==peers_before
 assert run('systemctl','show','xray.service','-p','MainPID','--value')==xray
 with sqlite3.connect(DB) as con:
  assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
  rows=con.execute('SELECT event_type,count(*) FROM subscription_notifications GROUP BY event_type').fetchall()
 report=dict(ok=True,version='1.74',scheduler_ok=True,vpn_peers_unchanged=True,notification_counts=dict(rows),backup=str(backup))
 (ROOT/'expiry-alerts-release.json').write_text(json.dumps(dict(release='2fbbaa1ac773dbca35356d69f0b0d08d119bdb3e',backup=str(backup))))
 Path('/root/vpn-expiry-alerts-result.json').write_text(json.dumps(report,indent=2))
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()
