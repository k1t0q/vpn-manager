"""Atomic static-asset update. No services restarted or client data changed."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import urlopen
import hashlib,json,os,shutil,tempfile
RELEASE="b45897cf23e6db5e6a2f894402f1fbad91fd6694"
FILES=[["server/connect.html","/opt/vpn-manager-connect/connect.html","4c788fcdb37eb4254198a84b348138f5a86350fb","f1acf74339da4a76888229805fe3e139c5874752"],["server/connect.js","/opt/vpn-manager-connect/connect.js","29f35ac3efa58f84ec022ad987e1a377e6aa0ce7","8e3eacba111a9a8712819b1c93fd0bd347dbe26f"],["server/connect.css","/opt/vpn-manager-connect/connect.css","62cf08099faaf60ff7a28ade36e2c2fb67e97d59","a6fa077e9d8b506b00f600d68467615fbd8c5da0"]]
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def write(p,b):
 fd,n=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o644);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(n,p)
def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 os.umask(0o077);data={}
 for source,target,old,new in FILES:
  p=Path(target)
  if p.is_symlink() or blob(p.read_bytes()) not in (old,new):raise SystemExit('Unexpected static asset')
  with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source,timeout=25) as r:b=r.read()
  assert blob(b)==new;data[target]=b
 backup=Path('/root/vpn-manager-backups')/('portal-compact-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700)
 for i,(_,target,_,_) in enumerate(FILES):shutil.copy2(target,backup/str(i))
 (backup/'files.json').write_text(json.dumps(FILES))
 try:
  for target,b in data.items():write(Path(target),b)
  with urlopen('https://connect.caesarbot.ru:8444/',timeout=20) as r:h=r.read().decode()
  assert 'id="download"' in h and 'id="qr"' in h and 'id="share"' not in h
  with urlopen('https://connect.caesarbot.ru:8444/assets/connect.css',timeout=20) as r:assert b'max-width:520px' in r.read()
 except Exception:
  for i,(_,target,_,_) in enumerate(FILES):write(Path(target),(backup/str(i)).read_bytes())
  raise
 print(json.dumps(dict(ok=True,compact=True,misleading_share_removed=True,services_restarted=False,client_data_untouched=True,backup=str(backup))))
if __name__=='__main__':main()
