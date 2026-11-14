"""Forms-only update. No client notes, subscriptions, database or VPN files modified."""
from datetime import datetime,timezone
import hashlib,os,shutil,tempfile
from pathlib import Path
from urllib.request import urlopen
ROOT=Path('/opt/vpn-manager-production/public')
FILES={'sqlite-manager.js':('aae67bc044fa7d7b7da92a21d1981582c1929779','5de60a45dc97a7bcda589a5492818e00f5558eff'),'index.html':('8ff865b8b6c3db19eebafbd9c7d1e7e13ab9e7d3','90e0ad4cfc3fc5550ab19b248ed05cdfec72ccf1')}
def blob(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def write(path,data):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.forms-')
    with os.fdopen(fd,'wb') as out:os.fchmod(out.fileno(),0o644);out.write(data);out.flush();os.fsync(out.fileno())
    os.replace(tmp,path)
def main():
    if os.geteuid()!=0:raise SystemExit('Root required')
    os.umask(0o077);materials={}
    for name,(old,new) in FILES.items():
        path=ROOT/name
        if path.is_symlink() or blob(path.read_bytes())!=old:raise SystemExit('Installed frontend differs; inspect before update')
        with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/e43a2988da36377b1c07b0fc880f805a9ea644ba/'+name,timeout=30) as response:data=response.read()
        if blob(data)!=new:raise SystemExit('Downloaded source mismatch')
        materials[name]=data
    backup=Path('/root/vpn-manager-backups')/('forms-simplified-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    for name in FILES:shutil.copy2(ROOT/name,backup/name)
    try:
        for name,data in materials.items():write(ROOT/name,data)
        for name,data in materials.items():
            if (ROOT/name).read_bytes()!=data:raise RuntimeError('Installed file mismatch')
    except Exception:
        for name in FILES:write(ROOT/name,(backup/name).read_bytes())
        raise SystemExit('Previous frontend restored')
    print('SIMPLIFIED_FORMS_READY; v1.73; notes preserved in database; existing subscriptions untouched; no restart')
    print('CODE_BACKUP: '+str(backup))
if __name__=='__main__':main()
