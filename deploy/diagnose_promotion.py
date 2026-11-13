"""Read-only diagnosis of a stopped promotion. No cleanup/restarts/secrets.

Only writes a sanitized report in the staging public directory and a private
copy under /root. The runtime probe imports three registry libraries as the
unprivileged live API identity; it does not import application code.
"""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import sqlite3
import subprocess
import tempfile


def command(args):
    return subprocess.run(args,capture_output=True,text=True,timeout=20)


def file_info(path):
    path=Path(path)
    if not path.exists() and not path.is_symlink():return dict(exists=False)
    stat=path.lstat()
    return dict(exists=True,uid=stat.st_uid,gid=stat.st_gid,mode=oct(stat.st_mode & 0o777),symlink=path.is_symlink())


def runtime_info(path,expected_uid=0):
    path=Path(path);result=file_info(path)
    if not path.is_dir() or path.is_symlink():return result
    bad_owner=[];writable=[]
    for item in [path,*path.rglob('*')]:
        stat=item.lstat();name='.' if item==path else str(item.relative_to(path))
        if stat.st_uid!=expected_uid:bad_owner.append(dict(path=name,uid=stat.st_uid))
        if not item.is_symlink() and stat.st_mode & 0o022:writable.append(name)
    result.update(non_root_owned=len(bad_owner),non_root_examples=bad_owner[:5],writable=len(writable),writable_examples=writable[:5])
    python=path/'bin/python'
    result['python_exists']=python.exists()
    if python.exists():result['python_target']=str(python.resolve())
    return result


def main():
    if os.geteuid()!=0:raise SystemExit('Root required for read-only inspection')
    addresses=command(['ip','-4','-o','address','show'])
    if '185.23.239.142/' not in addresses.stdout:raise SystemExit('Server identity mismatch')
    state=Path('/var/lib/vpn-manager-production');site=Path('/opt/vpn-manager-production')
    code=Path('/usr/local/libexec/vpn-manager-production');auth=Path('/var/lib/vpn-manager-production-auth')
    report=dict(generated_at=datetime.now(timezone.utc).isoformat(),server_ip='185.23.239.142')
    report['paths']={str(p):file_info(p) for p in (state,code,site,auth,state/'baseline.json',state/'activation.json',state/'operations/pending.json',auth/'sessions.db',Path('/etc/vpn-manager-live.env'),Path('/etc/vpn-manager-live-bridge.env'),Path('/root/vpn-manager-live-login-code'))}
    for name in ('baseline.json','activation.json'):
        path=state/name
        if path.is_file():
            data=json.loads(path.read_text())
            report[name]=dict(approved=data.get('approved'),original_clients=data.get('original_clients'),clients=len(data.get('clients',{})),expiry_holds=len(data.get('initial_expiry_hold',{})),release=data.get('release'))
    try:
        user=pwd.getpwnam('vpn-panel')
        report['live_user']=dict(exists=True,uid=user.pw_uid,gid=user.pw_gid,home=user.pw_dir,shell=user.pw_shell)
    except KeyError:report['live_user']=dict(exists=False)
    report['source_runtime']=runtime_info('/opt/vpn-manager-stage-venv')
    report['copied_runtime']=runtime_info(site/'venv')
    if report['live_user']['exists'] and (site/'venv/bin/python').exists():
        result=command(['runuser','-u','vpn-panel','--',str(site/'venv/bin/python'),'-c','import flask,requests,gunicorn;print("RUNTIME_IMPORT_OK")'])
        categories=[label for needle,label in [('Permission denied','permission_denied'),('PermissionError','permission_denied'),('No module named','module_missing'),('SyntaxError','python_syntax'),('shared libraries','shared_library')] if needle in result.stderr]
        report['runtime_probe']=dict(returncode=result.returncode,import_ok=result.stdout.strip()=='RUNTIME_IMPORT_OK',error_categories=sorted(set(categories)))
    report['services']={}
    for unit in ('vpn-manager.service','vpn-manager-panel-test.service','vpn-manager-awg-readonly.service','vpn-manager-live-bridge.service','vpn-manager-live-api.service','vpn-manager-preview-snapshot.service','xray.service','caddy.service'):
        data=command(['systemctl','show',unit,'-p','LoadState','-p','ActiveState','-p','SubState','-p','Result','-p','ExecMainStatus','-p','User','-p','MainPID'])
        report['services'][unit]=dict(line.split('=',1) for line in data.stdout.splitlines() if '=' in line)
    report['backups']=[]
    for folder in sorted(Path('/root/vpn-manager-backups').glob('live-promotion-*'))[-3:]:
        report['backups'].append(dict(path=str(folder),before_stop_backup=(folder/'clients-before-stop.db').exists(),after_stop_backup=(folder/'clients.db').exists(),rollback_plan=(folder/'rollback.json').exists()))
    database=Path('/opt/vpn-manager/data/clients-staging.db')
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as con:
        report['working_database']=dict(clients=con.execute('SELECT count(*) FROM clients').fetchone()[0],history=con.execute('SELECT count(*) FROM client_history').fetchone()[0],integrity_ok=con.execute('PRAGMA quick_check').fetchone()[0]=='ok')
    if (auth/'sessions.db').exists():
        with sqlite3.connect((auth/'sessions.db').as_uri()+'?mode=ro',uri=True) as con:
            table=con.execute("SELECT 1 FROM sqlite_master WHERE name='panel_sessions'").fetchone()
            report['live_session_count']=con.execute('SELECT count(*) FROM panel_sessions').fetchone()[0] if table else 0
    script=Path('/root/promote-vpn-live.py')
    if script.exists():report['installer_sha256']=hashlib.sha256(script.read_bytes()).hexdigest()
    public=Path('/srv/vpn-manager-stage/current/public').resolve()
    if not public.is_relative_to(Path('/srv/vpn-manager-stage/releases')):raise SystemExit('Unexpected staging report destination')
    data=json.dumps(report,ensure_ascii=False,indent=2).encode()
    for target,mode in ((Path('/root/vpn-promotion-diagnostic.json'),0o600),(public/'promotion-diagnostic.json',0o644)):
        fd,tmp=tempfile.mkstemp(dir=target.parent,prefix='.promotion-diagnostic-')
        with os.fdopen(fd,'wb') as out:
            os.fchmod(out.fileno(),mode);out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(tmp,target)
    print('PROMOTION_DIAGNOSTIC_READY; no services restarted; no production data changed')


if __name__=='__main__':main()
