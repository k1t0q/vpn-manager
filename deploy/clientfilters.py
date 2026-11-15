"""Pinned static UI update. No API/VPN restart or client data writes."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,sqlite3,subprocess,tempfile
from urllib.request import urlopen
RELEASE='17fcc530799b72a0e7b777d5aeb4259c117d0102'
PUBLIC=Path('/opt/vpn-manager-production/public')
FILES=[('sqlite-manager.js','57bc8665f184855eaae438289d153f70550bb308','82da9f6f2a9266624dd31d084e7b2df2da0d5bb6'),('index.html','bdc194d3455df250f2389100283bb6876be437a0','35bb2594dc299319c72ea211476e874f51584c45')]
def run(*a):return subprocess.run(a,check=True,capture_output=True,text=True,timeout=20).stdout.strip()
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def write(path,b):
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.filters-')
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o644);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(name,path)
os.umask(0o077)
materials={}
for name,old,new in FILES:
 p=PUBLIC/name
 if p.is_symlink() or blob(p.read_bytes())!=old:raise SystemExit('Installed UI differs; inspect first')
 with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+name,timeout=30) as r:b=r.read()
 if blob(b)!=new:raise SystemExit('Release digest mismatch')
 materials[name]=b
backup=Path('/root/vpn-manager-backups')/('client-filters-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700)
for name,old,new in FILES:(backup/name).write_bytes((PUBLIC/name).read_bytes())
with sqlite3.connect(backup/'clients.db') as dest:
 with sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True) as source:source.backup(dest)
 assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
config=Path('/etc/amnezia/amneziawg/awg-test.conf');before=config.read_bytes()
peers=set(run('awg','show','awg-test','peers').split())
pids={s:run('systemctl','show',s,'-p','MainPID','--value') for s in ['xray.service','vpn-manager-live-bridge.service','vpn-manager-live-api.service']}
profiles={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')}
try:
 for name,old,new in FILES:write(PUBLIC/name,materials[name])
 with urlopen('https://185-23-239-142.sslip.io:8444/panel-live/',timeout=10) as response:html=response.read()
 assert b'v1.76' in html and b'value="expiry"' in html
 assert config.read_bytes()==before and set(run('awg','show','awg-test','peers').split())==peers
 assert all(run('systemctl','show',s,'-p','MainPID','--value')==pid for s,pid in pids.items())
 assert all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in profiles.items())
except Exception:
 for name,old,new in FILES:write(PUBLIC/name,(backup/name).read_bytes())
 raise SystemExit('Previous UI restored; database and VPN state were not restored') from None
report=dict(ok=True,version='1.76',filters=['expiry','all','referrals'],mobile_import_hidden=True,configs_unchanged=True,vpn_unchanged=True,services_not_restarted=True,backup=str(backup))
Path('/root/vpn-client-filters-result.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
