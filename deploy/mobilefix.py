"""Frontend-only mobile duplicate-label removal. No VPN or database changes."""
from datetime import datetime,timezone
import hashlib,os,shutil,tempfile
from pathlib import Path
from urllib.request import urlopen
ROOT=Path('/opt/vpn-manager-production/public')
FILES={'sqlite-manager.js':('f4ccc4084f326cf224c1988813d102a04560774a9a5d481a755395e375240df3','aae67bc044fa7d7b7da92a21d1981582c1929779'),'index.html':('ac203627f96b15ed699921d6f84be2b612fa792478c9ac7a3b5b08862ec84bf3','8ff865b8b6c3db19eebafbd9c7d1e7e13ab9e7d3')}
def write(path,data):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.mobile-status-')
    with os.fdopen(fd,'wb') as out:os.fchmod(out.fileno(),0o644);out.write(data);out.flush();os.fsync(out.fileno())
    os.replace(tmp,path)
def main():
    if os.geteuid()!=0:raise SystemExit('Root required')
    os.umask(0o077);materials={}
    for name,(old,blob) in FILES.items():
        path=ROOT/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=old:raise SystemExit('Installed frontend changed; review first')
        with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/f73229773e699a6a5958d52ce8fe5f357b84f1ab/'+name,timeout=30) as response:data=response.read()
        if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=blob:raise SystemExit('Source digest mismatch')
        materials[name]=data
    backup=Path('/root/vpn-manager-backups')/('mobile-status-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    for name in FILES:shutil.copy2(ROOT/name,backup/name)
    try:
        for name,data in materials.items():write(ROOT/name,data)
        for name,data in materials.items():
            if (ROOT/name).read_bytes()!=data:raise RuntimeError('Installed source mismatch')
    except Exception:
        for name in FILES:write(ROOT/name,(backup/name).read_bytes())
        raise SystemExit('Previous frontend restored')
    print('MOBILE_STATUS_READY; v1.72; VPN, profiles, QR and database untouched; no restart')
    print('CODE_BACKUP: '+str(backup))
if __name__=='__main__':main()
