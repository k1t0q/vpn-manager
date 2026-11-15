"""Code-only frontend sorting update. No service restart or client-data write."""
from datetime import datetime,timezone
import hashlib,os,shutil,tempfile
from pathlib import Path
from urllib.request import urlopen
ROOT=Path('/opt/vpn-manager-production/public')
FILES={
'sqlite-manager.js':('734b2a310d9a42ccef4d1a5365d1177c6bea827795496f9bb1247dd89db79d83','f4ccc4084f326cf224c1988813d102a04560774a9a5d481a755395e375240df3'),
'index.html':('3ba86165c5284073bbbd51fe8a00a22d3545f4039e27838fd563ba96c86f5273','ac203627f96b15ed699921d6f84be2b612fa792478c9ac7a3b5b08862ec84bf3')}
def write(path,data):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.newest-')
    with os.fdopen(fd,'wb') as out:os.fchmod(out.fileno(),0o644);out.write(data);out.flush();os.fsync(out.fileno())
    os.replace(tmp,path)
def main():
    if os.geteuid()!=0:raise SystemExit('Root required')
    os.umask(0o077);materials={}
    for name,(old,new) in FILES.items():
        path=ROOT/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=old:raise SystemExit('Installed frontend changed; inspect before update')
        with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/fb36b7e3cd04ad3e1e9f909ed021183f17aa8574/'+name,timeout=30) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=new:raise SystemExit('Downloaded source digest mismatch')
        materials[name]=data
    backup=Path('/root/vpn-manager-backups')/('newest-first-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    for name in FILES:shutil.copy2(ROOT/name,backup/name)
    try:
        for name,data in materials.items():write(ROOT/name,data)
        for name,(old,new) in FILES.items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=new:raise RuntimeError('Installed file digest mismatch')
    except Exception:
        for name in FILES:write(ROOT/name,(backup/name).read_bytes())
        raise SystemExit('Previous frontend restored; client data untouched')
    print('NEWEST_CLIENTS_FIRST_READY; v1.71; no services restarted; no client state changed')
    print('CODE_BACKUP: '+str(backup))
if __name__=='__main__':main()
