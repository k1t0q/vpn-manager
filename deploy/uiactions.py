"""Code-only UI update. No client DB writes or VPN/service restarts."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import urlopen
import hashlib,json,os,shutil,tempfile
RELEASE="c6ce100c598ae5a60ac772e7300a1c5f3b64bafa"
FILES=[["index.html","/opt/vpn-manager-production/public/index.html","86270637f03393c9486ab532cfd4a691795907ac","249945465587beec4c8681814d1e74451d5c5f81"],["sqlite-manager.js","/opt/vpn-manager-production/public/sqlite-manager.js","307b05babf42d20329510b8a10d9e3f6bf26f690","bc0511813aa5e7ec86c958faef4475cffe45a0fb"],["server/connect.html","/opt/vpn-manager-connect/connect.html","e45c680a8c41b16dc03a37db852a944e2fff0b51","f17f4c59f9bb1cb30864eef773ce9c9697a46d70"],["server/connect.js","/opt/vpn-manager-connect/connect.js","f26f0a23dc3879ebe3bb01eaf7896131134c802c","ebc84dd09f2832f381fa8c8715c10baf88f6ba5e"]]
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def write(p,b):
 fd,n=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f:
  os.fchmod(f.fileno(),0o644);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(n,p)
def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 os.umask(0o077)
 data={}
 for source,target,old,new in FILES:
  p=Path(target)
  if p.is_symlink() or blob(p.read_bytes()) not in {old,new}:raise SystemExit('Unexpected current code: '+source)
  with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source,timeout=25) as r:b=r.read()
  assert blob(b)==new
  data[target]=b
 backup=Path('/root/vpn-manager-backups')/('ui-actions-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
 backup.mkdir(mode=0o700)
 for i,(_,target,_,_) in enumerate(FILES):shutil.copy2(target,backup/str(i))
 (backup/'files.json').write_text(json.dumps(FILES))
 try:
  for target,b in data.items():write(Path(target),b)
  with urlopen('https://185-23-239-142.sslip.io:8444/panel-live/',timeout=20) as r:
   h=r.read().decode();assert 'v1.78' in h and '<option value="all" selected>Все</option>' in h
  with urlopen('https://connect.caesarbot.ru:8444/',timeout=20) as r:
   h=r.read().decode();assert 'компьютере и телефоне' in h and 'на iPhone' not in h
 except Exception:
  for i,(_,target,_,_) in enumerate(FILES):write(Path(target),(backup/str(i)).read_bytes())
  raise
 print(json.dumps(dict(ok=True,version='1.78',backup=str(backup),vpn_untouched=True,services_restarted=False)))
if __name__=='__main__':main()
