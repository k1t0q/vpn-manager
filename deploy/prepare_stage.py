#!/usr/bin/python3
"""Reviewed, one-time root bootstrap. Requires explicit SSH-access approval.

Does not touch production code, AWG, Xray, Caddy or existing service definitions.
Installs a new, isolated preview service; HTTP routing is a separate operation.
"""
import argparse
import hashlib
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess

ROOT=Path('/srv/vpn-manager-stage')
HERE=Path(__file__).resolve().parent


def run(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=120).stdout.strip()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--public-key',required=True,type=Path)
    parser.add_argument('--install-approved',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0 or not args.install_approved:
        raise SystemExit('Root and explicit approval required')
    key=args.public_key.read_text().strip()
    if not key.startswith('ssh-rsa ') or len(key.split()) not in (2,3) or '\n' in key:
        raise SystemExit('Invalid deployment public key')
    # Never read or display any login password/API token. Reuse only panel login code.
    pid=int(run('systemctl','show','vpn-manager-panel-test.service','-p','MainPID','--value'))
    if pid<=1:raise SystemExit('Existing panel service must be active')
    environ={}
    for item in Path('/proc/'+str(pid)+'/environ').read_bytes().split(b'\0'):
        if b'=' in item:
            k,v=item.split(b'=',1);environ[k.decode()]=v.decode()
    code=environ.get('PANEL_ADMIN_CODE','')
    if not code or '\n' in code or '\r' in code or '\0' in code:
        raise SystemExit('Panel login code unavailable; no changes made')
    # Avoid silently replacing any pre-existing preview setup.
    if Path('/etc/vpn-manager-stage.env').exists():
        raise SystemExit('Preview already configured; inspect before replacing')
    os.umask(0o077)
    venv=Path('/opt/vpn-manager-stage-venv')
    if not venv.exists():run('python3','-m','venv',str(venv))
    run(str(venv/'bin/pip'),'install','-r',str(HERE/'staging-requirements.txt'))
    try:pwd.getpwnam('vpn-deploy')
    except KeyError:pass
    else:raise SystemExit('Existing vpn-deploy account needs review; not modified')
    run('useradd','--system','--create-home','--home-dir','/home/vpn-deploy','--shell','/bin/bash','vpn-deploy')
    account=pwd.getpwnam('vpn-deploy')
    home=Path('/home/vpn-deploy')
    home.chmod(0o755);os.chown(home,0,0)
    ssh=home/'.ssh';ssh.mkdir(mode=0o755,exist_ok=True);os.chown(ssh,0,0);ssh.chmod(0o755)
    authorized=ssh/'authorized_keys'
    if authorized.exists():raise SystemExit('Existing SSH authorization needs review')
    authorized.write_text('restrict,command="/usr/bin/python3 /usr/local/libexec/vpn-staging-gate.py" '+key+'\n')
    authorized.chmod(0o644);os.chown(authorized,0,0)
    ROOT.mkdir(mode=0o755,exist_ok=True);ROOT.chmod(0o755);os.chown(ROOT,account.pw_uid,account.pw_gid)
    data=ROOT/'data';data.mkdir(mode=0o700,exist_ok=True);os.chown(data,account.pw_uid,account.pw_gid)
    target=data/'clients.db'
    if target.exists():raise SystemExit('Preview DB exists; will not overwrite')
    source=sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True)
    with sqlite3.connect(target) as destination:
        source.backup(destination)
        # Sessions are credentials and must not be carried into the preview clone.
        if destination.execute("SELECT 1 FROM sqlite_master WHERE name='panel_sessions'").fetchone():
            destination.execute('DELETE FROM panel_sessions')
        if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('Copy integrity failure')
    source.close();target.chmod(0o600);os.chown(target,account.pw_uid,account.pw_gid)
    # systemd EnvironmentFile supports double-quoted escaped values.
    quoted=code.replace('\\','\\\\').replace('"','\\"')
    env=Path('/etc/vpn-manager-stage.env')
    env.write_text('VPN_MANAGER_DB='+str(target)+'\nPANEL_ADMIN_CODE="'+quoted+'"\nPANEL_ORIGIN=https://185-23-239-142.sslip.io:8443\nPANEL_AWG_URL=http://127.0.0.1:1\nPANEL_AWG_TOKEN=\n')
    env.chmod(0o600)
    gate=Path('/usr/local/libexec/vpn-staging-gate.py');gate.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(HERE/'staging_gate.py',gate);gate.chmod(0o755)
    unit=Path('/etc/systemd/system/vpn-manager-stage.service')
    shutil.copyfile(HERE/'vpn-manager-stage.service',unit);unit.chmod(0o644)
    requirement_hash=hashlib.sha256((HERE.parent/'server/requirements.txt').read_bytes()).hexdigest()
    Path('/etc/vpn-manager-stage.requirements.sha256').write_text(requirement_hash+'\n')
    Path('/etc/vpn-manager-stage.requirements.sha256').chmod(0o644)
    sudoers=Path('/etc/sudoers.d/vpn-manager-stage')
    sudoers.write_text('vpn-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart vpn-manager-stage.service\n')
    sudoers.chmod(0o440)
    run('visudo','-cf',str(sudoers))
    run('systemctl','daemon-reload')
    # Deliberately neither start nor enable before first successful release.
    print('STAGING_ACCOUNT_READY; production services unchanged')


if __name__=='__main__':main()
