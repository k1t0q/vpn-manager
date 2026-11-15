"""Frontend-only release. No services or VPN restarted."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import build_opener,ProxyHandler
import hashlib,json,os,shutil,sqlite3,subprocess,tempfile
RELEASE='2f90a862d93ec6d15d64596ead1bd6dbaa68a42a'
FILES=[
('index.html','/opt/vpn-manager-production/public/index.html','30c95c8ae795643c04912dead0f801df62d32f6c','e07eb8a3a92beaf27682ac205163d2c29f476ad4','https://185-23-239-142.sslip.io:8444/panel-live/'),
('sqlite-manager.js','/opt/vpn-manager-production/public/sqlite-manager.js','c7dab3647ef90ba39f236dc03e18b2400f915579','b0cef870abafe3d7b7e2d9d5b08f8b926e75a0f1','https://185-23-239-142.sslip.io:8444/panel-live/sqlite-manager.js'),
('server/connect.css','/opt/vpn-manager-connect/connect.css','5140c4211fe8f8020cb48c5cd9e0bd4598392d8e','29a7f771cdf9f822dd2c7c1c5f79d35ba8876c98','https://connect.caesarbot.ru:8444/assets/connect.css')]
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def write(p,b):
 fd,n=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o644);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(n,p)
def main():
 assert os.geteuid()==0;os.umask(0o077);opener=build_opener(ProxyHandler({}));data={}
 for source,target,old,new,url in FILES:
  p=Path(target);assert not p.is_symlink() and blob(p.read_bytes())==old
  with opener.open('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source,timeout=30) as r:b=r.read()
  assert blob(b)==new;data[target]=b
 backup=Path('/root/vpn-manager-backups')/('buttons-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700)
 for i,(_,target,_,_,_) in enumerate(FILES):shutil.copy2(target,backup/str(i))
 (backup/'files.json').write_text(json.dumps(FILES))
 db=Path('/opt/vpn-manager/data/clients-staging.db')
 with sqlite3.connect(backup/'clients.db') as dest,sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src:src.backup(dest)
 protected=[Path('/etc/amnezia/amneziawg/awg-test.conf'),*Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')]
 hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
 try:
  for target,b in data.items():write(Path(target),b)
  for _,_,_,new,url in FILES:
   with opener.open(url,timeout=25) as r:assert blob(r.read())==new
  with sqlite3.connect(backup/'clients.db') as old,sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as live:
   for table in ('clients','client_history'):assert old.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()==live.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()
  assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
  print(json.dumps(dict(ok=True,copy_feedback_in_dialog=True,uniform_portal_buttons=True,services_restarted=False,client_data_unchanged=True,backup=str(backup))))
 except Exception:
  for i,(_,target,_,_,_) in enumerate(FILES):write(Path(target),(backup/str(i)).read_bytes())
  raise SystemExit('Previous frontend restored; client data unchanged') from None
if __name__=='__main__':main()
