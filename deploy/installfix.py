"""Pinned root engine hotfix. Restarts only VPN MANAGER bridge, never AWG/Xray."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.request import Request,urlopen
CODE=Path('/usr/local/libexec/vpn-manager-production')
STATE=Path('/var/lib/vpn-manager-production')
DB=Path('/opt/vpn-manager/data/clients-staging.db')
CHANGES={'awg_persistence.py':('d2bbe55e9b03ec95a17fc0e3087d9ad6b0b6d5bd53a7d854372d9494fea543a7','b05939913926eb990bc51a31b0d80e054fd0a9315c5bf978c2d7101f39965b61'),
'awg_delete.py':('bf1daaf74d0735c07327d8bfa9d4b949cedd0d28c252057f7804ac456b26a866','fc76bdb7a04e13ce951b0c31ab3a66123c2fca8896f4bc0d8a377a82ca431f1c')}
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=40).stdout.strip()
def main():
    if os.geteuid()!=0:raise SystemExit('Root required')
    os.umask(0o077);os.environ['NO_PROXY']='127.0.0.1,localhost'
    activation=json.loads((STATE/'activation.json').read_text())
    if activation.get('approved') is not True:raise SystemExit('No approved activation')
    if (STATE/'operations/pending.json').exists():raise SystemExit('Pending operation; inspect before patch')
    original=json.loads((STATE/'baseline.json').read_text())
    keys=set(run('awg','show','awg-test','peers').split())
    if not set(original['runtime_peers']).issubset(keys):raise SystemExit('Original runtime drift')
    xray=run('systemctl','show','xray.service','-p','MainPID','--value')
    materials={}
    for name,(old,new) in CHANGES.items():
        if hashlib.sha256((CODE/name).read_bytes()).hexdigest()!=old:raise SystemExit('Installed engine is not expected release')
        with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/05ade668c447f1af46dece59c6a5d9fc453e4b9c/server/'+name,timeout=30) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=new:raise SystemExit('Hotfix digest mismatch')
        compile(data,name,'exec');materials[name]=data
    backup=Path('/root/vpn-manager-backups')/('engine-bindmount-fix-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    for name in CHANGES:shutil.copy2(CODE/name,backup/name)
    run('systemctl','stop','vpn-manager-live-bridge.service')
    try:
        with sqlite3.connect(backup/'clients.db') as target:
            with sqlite3.connect(DB) as source:source.backup(target)
        for name,data in materials.items():
            fd,tmp=tempfile.mkstemp(dir=CODE,prefix='.hotfix-')
            with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
            os.replace(tmp,CODE/name)
        run('systemctl','start','vpn-manager-live-bridge.service')
        env=dict(line.split('=',1) for line in Path('/etc/vpn-manager-live-bridge.env').read_text().splitlines() if '=' in line)
        for attempt in range(30):
            try:
                request=Request('http://127.0.0.1:5055/api/managed/health',headers={'X-VPN-Token':env['PANEL_AWG_TOKEN']})
                with urlopen(request,timeout=3) as response:health=json.load(response)
                if health.get('scheduler_ok'):break
            except OSError:pass
            time.sleep(1)
        else:raise RuntimeError('Patched bridge health failed')
    except Exception:
        run('systemctl','stop','vpn-manager-live-bridge.service')
        for name in CHANGES:shutil.copy2(backup/name,CODE/name)
        run('systemctl','start','vpn-manager-live-bridge.service')
        raise SystemExit('Hotfix rolled back; inspect private backup')
    assert keys==set(run('awg','show','awg-test','peers').split())
    assert xray==run('systemctl','show','xray.service','-p','MainPID','--value')
    (STATE/'engine-hotfix.json').write_text(json.dumps(dict(release='05ade668c447f1af46dece59c6a5d9fc453e4b9c',backup=str(backup),digests={name:item[1] for name,item in CHANGES.items()})))
    print('ENGINE_BINDMOUNT_FIX_READY; original runtime peers unchanged; Xray unchanged')
    # Clean only disposable verification profiles left by the failed smoke test.
    with sqlite3.connect(DB) as con:
        deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
        rows=con.execute("SELECT id,name FROM clients WHERE name LIKE 'LIVE VERIFICATION %'"+(' AND id NOT IN (SELECT client_id FROM awg_deleted_clients)' if deleted else '')).fetchall()
    for cid,name in rows:
        if str(cid) in original['clients']:raise SystemExit('Protected cleanup target')
        payload=b'{}';key='verification-cleanup-'+secrets.token_hex(16)
        request=Request('http://127.0.0.1:5055/api/managed/clients/'+str(cid)+'/delete',data=payload,headers={'X-VPN-Token':env['PANEL_AWG_TOKEN'],'Idempotency-Key':key,'Content-Type':'application/json'},method='POST')
        with urlopen(request,timeout=30) as response:result=json.load(response)
        assert result.get('deleted') is True
    print('DISPOSABLE_VERIFICATION_CLEANUP_READY; removed='+str(len(rows)))
if __name__=='__main__':main()
