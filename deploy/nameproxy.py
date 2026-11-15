"""Update only the unprivileged API safe field allowlist; no VPN/state changes."""
from datetime import datetime,timezone
import hashlib,json,os,shutil,subprocess,tempfile,time
from pathlib import Path
from urllib.request import urlopen
TARGET=Path('/opt/vpn-manager-production/server/staging_api.py')
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=30)
def write(data):
    fd,tmp=tempfile.mkstemp(dir=TARGET.parent,prefix='.profile-name-')
    with os.fdopen(fd,'wb') as out:os.fchmod(out.fileno(),0o644);out.write(data);out.flush();os.fsync(out.fileno())
    os.replace(tmp,TARGET)
def main():
    if os.geteuid()!=0:raise SystemExit('Root required')
    os.umask(0o077);os.environ['NO_PROXY']='127.0.0.1,localhost'
    original=TARGET.read_bytes()
    if hashlib.sha256(original).hexdigest()!='4a6e6208803210385237f53c94d01f6d9cffafa0d5db235f575ce883dd3cd8dd':raise SystemExit('Unexpected installed API source')
    with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/a816083ae1c23c343a960776ec6f85ced4cd2dbb/server/staging_api.py',timeout=30) as response:data=response.read()
    if hashlib.sha256(data).hexdigest()!='0d93cadae009b1e78c60434913cc945a14fc6a96b0468249d50710caa1ffb36c':raise SystemExit('API source digest mismatch')
    compile(data,'staging_api.py','exec')
    backup=Path('/root/vpn-manager-backups')/('profile-name-proxy-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700);shutil.copy2(TARGET,backup/TARGET.name)
    try:
        write(data);run('systemctl','restart','vpn-manager-live-api.service')
        for _ in range(20):
            try:
                with urlopen('http://127.0.0.1:5056/health',timeout=3) as response:health=json.load(response)
                if health.get('ok') and health.get('scheduler_ok'):break
            except OSError:pass
            time.sleep(1)
        else:raise RuntimeError('API health failed')
    except Exception:
        write(original);run('systemctl','restart','vpn-manager-live-api.service');raise SystemExit('API source restored; VPN untouched')
    print('PROFILE_NAME_PROXY_READY; only unprivileged API restarted')
if __name__=='__main__':main()
