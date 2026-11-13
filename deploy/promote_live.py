"""Pinned owner-approved promotion. No AWG/Xray restart, key rotation or DB restore.

Default mode only checks prerequisites. Activation requires --activate-approved.
Rollback restores code routing/services only; it never replaces client data.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen

# Filled only after the source release has been committed and checked.
COMMIT='PENDING_REVIEWED_SOURCE'
FILES={}
STATE=Path('/var/lib/vpn-manager-production')
CODE=Path('/usr/local/libexec/vpn-manager-production')
SITE=Path('/opt/vpn-manager-production')
AUTH=Path('/var/lib/vpn-manager-production-auth')
DB=Path('/opt/vpn-manager/data/clients-staging.db')
PROFILES=Path('/opt/vpn-manager/awg-clients')
CONFIG=Path('/etc/amnezia/amneziawg/awg-test.conf')
STAGE=Path('/var/lib/vpn-manager-awg-stage')
OLD=('vpn-manager.service','vpn-manager-panel-test.service','vpn-manager-awg-readonly.service')
NEW=('vpn-manager-live-bridge.service','vpn-manager-live-api.service','vpn-manager-preview-snapshot.service')
EXPECTED_SOURCE={'app.py':'f06f51bfcf7edfa0a328de680a41d24dce2172c1024afedcbaac6023b758f8e4',
                 'awg_adapter.py':'cb4586942ed4a86ef27e94669f7621ea028f6025ce7efe1a074256ff46dad842'}


def run(*args, check=True):
    return subprocess.run(args,check=check,capture_output=True,text=True,timeout=40).stdout.strip()


def write(path, data, mode=0o600):
    path=Path(path);fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(name,path)
        fd=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if os.path.exists(name):os.unlink(name)


def backup_sqlite(source, target):
    with sqlite3.connect(target) as destination:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as connection:connection.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity failure')
    os.chmod(target,0o600)


def listeners(ports):
    selected=[]
    for line in run('ss','-ltnpH').splitlines():
        parts=line.split()
        if len(parts)>3 and re.search(r':('+('|'.join(map(str,ports)))+r')$',parts[3]):selected.append(line)
    return selected


def check_other_writers(allow_legacy=True):
    targets={str(DB)+suffix for suffix in ('','-wal','-shm')}
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name)==os.getpid():continue
        try:
            opened=any(os.readlink(fd) in targets for fd in (process/'fd').iterdir())
            if not opened:continue
            groups=(process/'cgroup').read_text()
            if allow_legacy and any('/'+old in groups for old in OLD):continue
            raise RuntimeError('Another process has the working SQLite open: PID '+process.name)
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue


def preflight():
    if STATE.exists() or CODE.exists() or SITE.exists() or AUTH.exists():raise RuntimeError('Production paths already exist; inspect before retrying')
    try:pwd.getpwnam('vpn-panel')
    except KeyError:pass
    else:raise RuntimeError('vpn-panel account already exists; review required')
    for name,digest in EXPECTED_SOURCE.items():
        if hashlib.sha256((Path('/opt/vpn-manager')/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Legacy production source changed')
    if not re.search(r'\binet 185\.23\.239\.142/',run('ip','-4','-o','address','show')):raise RuntimeError('Server identity mismatch')
    if (STAGE/'operations/pending.json').exists():raise RuntimeError('Pending staging operation')
    with sqlite3.connect((STAGE/'clients.db').as_uri()+'?mode=ro',uri=True) as con:
        ids={str(row[0]) for row in con.execute('SELECT id FROM clients WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients)')}
    protected=set(map(str,json.loads((STAGE/'baseline.json').read_text())['protected_ids']))
    if len(protected)!=59 or ids!=protected:raise RuntimeError('Clean up active test profiles before promotion')
    for unit in (*OLD,*NEW):
        if unit in NEW:
            if Path('/etc/systemd/system',unit).exists():raise RuntimeError('Live unit already exists')
            continue
        if run('systemctl','is-active',unit)!='active':raise RuntimeError('Expected old API is not active')
        text=run('systemctl','cat',unit)
        if re.search(r'(?im)^Exec\w*=.*\b(awg-quick|wg-quick|xray)\b',text):raise RuntimeError('Legacy API lifecycle includes VPN commands; review required')
        dependencies=run('systemctl','show',unit,'-p','Wants','-p','Requires')
        if 'awg-quick@' in dependencies or 'xray.service' in dependencies:raise RuntimeError('Legacy API pulls in VPN services')
        if Path('/etc/systemd/system',unit+'.d/live-writer.conf').exists():raise RuntimeError('Writer gate already exists')
    if listeners((5055,5056)):raise RuntimeError('New loopback ports already occupied')
    check_other_writers()
    for folder in ('/etc/cron.d','/etc/cron.daily','/etc/cron.hourly','/var/spool/cron/crontabs'):
        for path in Path(folder).glob('*'):
            if path.is_file() and re.search(r'vpn-manager|clients-staging\.db|127\.0\.0\.1:505[02]',path.read_text(errors='replace')):
                raise RuntimeError('Review a legacy scheduled job before promotion: '+str(path))
    if re.search(r'vpn-manager|awg-test',run('systemctl','list-timers','--all','--no-legend')):raise RuntimeError('Review existing VPN timers before promotion')
    for path in (Path('/opt/vpn-manager/app.py'),Path('/opt/vpn-manager/awg_adapter.py'),Path('/opt/vpn-manager-panel-test/server/staging_api.py')):
        if path.exists() and re.search(r'\b(?:awg-quick|wg-quick)\b',path.read_text()):raise RuntimeError('Review lifecycle commands in legacy API before promotion: '+str(path))
    admin=next((line for line in Path('/etc/vpn-manager-stage.env').read_text().splitlines() if line.startswith('PANEL_ADMIN_CODE=')),None)
    if not admin or not admin.split('=',1)[1].strip():raise RuntimeError('Existing admin login is not configured')
    expected=Path('/etc/vpn-manager-stage.requirements.sha256').read_text().strip()
    if FILES.get('server/requirements.txt')!=expected:raise RuntimeError('Reviewed dependency set does not match installed environment')
    if Path('/etc/caddy/vpn-manager-live.caddy').exists():raise RuntimeError('Live HTTP snippet already exists')
    for unit in ('awg-quick@awg-test.service','xray.service'):
        dependencies=run('systemctl','show',unit,'-p','Requires','-p','BindsTo','-p','PartOf')
        if any(old in dependencies for old in OLD):raise RuntimeError('VPN depends on a legacy API unit')
    return admin


def materialize():
    if not re.fullmatch(r'[0-9a-f]{40}',COMMIT) or not FILES:raise RuntimeError('Unpinned promotion source')
    materials={}
    for path,digest in FILES.items():
        with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+COMMIT+'/'+path,timeout=30) as response:data=response.read(250001)
        if len(data)>250000 or hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError('Release digest mismatch')
        materials[path]=data
    return materials


def baseline(now):
    from awg_persistence import AWGRuntime,peer_blocks,subscription_deadline
    header,blocks=peer_blocks(CONFIG.read_text())
    if re.search(r'(?im)^\s*SaveConfig\s*=\s*(true|yes|on|1)\s*$',header):raise RuntimeError('SaveConfig requires review')
    runtime=AWGRuntime();live=runtime.peers()
    if run('awg','show','awg-test','listen-port')!='51840':raise RuntimeError('Unexpected VPN port')
    disk={key:fields for key,fields,_ in blocks}
    with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row;rows=con.execute('SELECT * FROM clients ORDER BY id').fetchall()
    if len(rows)!=59:raise RuntimeError('Expected exactly 59 original clients')
    clients={};hold={};addresses=set();keys=set()
    for row in rows:
        cid=str(row['id']);folder=PROFILES/cid
        if folder.is_symlink() or any(p.is_symlink() for p in folder.rglob('*')):raise RuntimeError('Profile symlink requires review')
        meta=json.loads((folder/'metadata.json').read_text());key=meta['public_key'];address=meta['address']
        if str(meta['client_id'])!=cid or key not in live or disk.get(key,{}).get('AllowedIPs')!=address+'/32' or meta.get('enabled') is False:raise RuntimeError('Original profile consistency failure')
        if key in keys or address in addresses:raise RuntimeError('Duplicate original mapping')
        keys.add(key);addresses.add(address)
        clients[cid]=dict(public_key=key,address=address,peer_fields=disk[key])
        end=subscription_deadline(row['period'],row['end_date'])
        if end is not None and end<=now:hold[cid]=row['end_date']
    if len(hold)!=5:raise RuntimeError('Expired-client count changed; review before promotion')
    owner=next((row['user_id'] for row in rows if row['user_id'] and str(row['user_id']).strip()),None)
    if not owner:raise RuntimeError('Trusted owner unavailable')
    return dict(clients=clients,runtime_peers=sorted(live),unmanaged_disk_peers={k:v for k,v in disk.items() if k not in keys},initial_expiry_hold=hold),owner,live


def unit(description,command,user='root',env='',rw='',after='network.target'):
    return f'''[Unit]
Description={description}
After={after}
[Service]
User={user}
WorkingDirectory={CODE if user=='root' else SITE/'server'}
EnvironmentFile={env}
ExecStart={command}
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
CapabilityBoundingSet={'CAP_NET_ADMIN' if user=='root' else ''}
ReadWritePaths={rw}
[Install]
WantedBy=multi-user.target
'''


def health(url,token=None,require_scheduler=False):
    req=Request(url,headers={'X-VPN-Token':token} if token else {})
    for attempt in range(20):
        try:
            with urlopen(req,timeout=3) as response:data=json.load(response)
            if data.get('ok') and (not require_scheduler or data.get('scheduler_ok')):return data
        except (OSError,ValueError):pass
        time.sleep(1)
    raise RuntimeError('New service health check failed')


def rollback(backup):
    """Restore API/routing configuration only; preserve newer VPN and SQL state."""
    if backup.parent!=Path('/root/vpn-manager-backups') or not re.fullmatch(r'live-promotion-[0-9TZ]+',backup.name):raise RuntimeError('Invalid rollback directory')
    plan=json.loads((backup/'rollback.json').read_text())
    for name in NEW:run('systemctl','disable','--now',name,check=False)
    # Restore routing before reopening legacy writers; never touch VPN files/DB.
    write('/etc/caddy/Caddyfile',(backup/'Caddyfile').read_bytes(),plan['caddy_mode'])
    Path('/etc/caddy/vpn-manager-live.caddy').unlink(missing_ok=True)
    run('caddy','validate','--config','/etc/caddy/Caddyfile','--adapter','caddyfile')
    run('caddy','reload','--config','/etc/caddy/Caddyfile','--adapter','caddyfile')
    for old in OLD:Path('/etc/systemd/system',old+'.d/live-writer.conf').unlink(missing_ok=True)
    (STATE/'activation.json').unlink(missing_ok=True)
    run('systemctl','daemon-reload')
    for old,state in plan['old_enabled'].items():
        if state=='enabled':run('systemctl','enable',old)
        run('systemctl','start',old)
    print('LIVE_CODE_ROLLBACK_READY; client databases and VPN configuration were not restored')


def activate(materials,admin):
    from awg_persistence import AWGRuntime
    import route_stage
    before_config=CONFIG.read_bytes();base,owner,live=baseline(datetime.now(timezone.utc))
    backup=Path('/root/vpn-manager-backups')/('live-promotion-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True,mode=0o700)
    caddy=Path('/etc/caddy/Caddyfile');caddy_before=caddy.read_bytes()
    old_enabled={old:run('systemctl','is-enabled',old,check=False) for old in OLD}
    plan=dict(old_enabled=old_enabled,caddy_mode=caddy.stat().st_mode & 0o777)
    write(backup/'rollback.json',json.dumps(plan).encode());write(backup/'Caddyfile',caddy_before)
    write(backup/'awg-test.conf',before_config)
    with tarfile.open(backup/'original-files.tar.gz','w:gz') as archive:
        for path in (PROFILES,Path('/opt/vpn-manager/app.py'),Path('/opt/vpn-manager/awg_adapter.py')):archive.add(path,arcname=str(path).lstrip('/'))
        for old in OLD:
            path=Path('/etc/systemd/system',old)
            if path.exists():archive.add(path,arcname=str(path).lstrip('/'))
    backup_sqlite(DB,backup/'clients-before-stop.db')
    # All preparation is complete before stopping a writer. No VPN unit is started.
    for directory,mode in ((STATE,0o700),(CODE,0o700),(SITE,0o755),(SITE/'server',0o755),(SITE/'public',0o755),(AUTH,0o700)):
        directory.mkdir(mode=mode)
    for path,data in materials.items():
        if path.endswith('.py') and not path.startswith('tests/'):
            write(CODE/Path(path).name,data)
        if path.startswith('server/') and path.endswith('.py'):
            write(SITE/'server'/Path(path).name,data,0o644)
        if '/' not in path:write(SITE/'public'/path,data,0o644)
    write(SITE/'public/config.js',b'window.VPN_MANAGER_CONFIG={API_BASE:"/live-api"};\n',0o644)
    write(STATE/'template.conf',(PROFILES/next(iter(base['clients']))/'client.conf').read_bytes())
    write(STATE/'baseline.json',json.dumps(base).encode())
    run('useradd','--system','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','vpn-panel')
    account=pwd.getpwnam('vpn-panel');os.chown(AUTH,account.pw_uid,account.pw_gid)
    with sqlite3.connect(AUTH/'sessions.db') as con:con.execute('CREATE TABLE panel_sessions(token_hash TEXT PRIMARY KEY,expires_at REAL NOT NULL)')
    os.chmod(AUTH/'sessions.db',0o600);os.chown(AUTH/'sessions.db',account.pw_uid,account.pw_gid)
    token=secrets.token_urlsafe(48)
    write('/etc/vpn-manager-live-bridge.env',('PANEL_AWG_TOKEN='+token+'\n').encode())
    env=admin+'\nVPN_MANAGER_DB='+str(AUTH/'sessions.db')+'\nPANEL_ORIGIN=https://185-23-239-142.sslip.io:8443\nPANEL_AWG_URL=http://127.0.0.1:5055\nPANEL_AWG_TOKEN='+token+'\n'
    write('/etc/vpn-manager-live.env',env.encode())
    definitions={
        NEW[0]:unit('VPN MANAGER frozen production peer controller','/usr/bin/python3 '+str(CODE/'awg_production_server.py'),env='/etc/vpn-manager-live-bridge.env',rw=str(STATE)+' '+str(DB.parent)+' '+str(PROFILES)+' '+str(CONFIG.parent),after='network.target awg-quick@awg-test.service'),
        NEW[1]:unit('VPN MANAGER pinned production API','/opt/vpn-manager-stage-venv/bin/gunicorn --workers 1 --bind 127.0.0.1:5056 production_app:app',user='vpn-panel',env='/etc/vpn-manager-live.env',rw=str(AUTH),after='network.target '+NEW[0]),
        NEW[2]:unit('VPN MANAGER read-only preview snapshot','/usr/bin/python3 '+str(CODE/'preview_snapshot.py'),env='/etc/vpn-manager-awg-readonly.env',rw=str(STAGE))}
    for name,text in definitions.items():write(Path('/etc/systemd/system',name),text.encode(),0o644)
    for old in OLD:
        folder=Path('/etc/systemd/system',old+'.d');folder.mkdir(exist_ok=True)
        write(folder/'live-writer.conf',('[Unit]\nConditionPathExists=!'+str(STATE/'activation.json')+'\n').encode(),0o644)
    try:
        for old in OLD:run('systemctl','stop',old)
        if listeners((5050,5052,5054)):raise RuntimeError('An unexpected legacy listener remains; no process was killed')
        check_other_writers(allow_legacy=False)
        if (STAGE/'operations/pending.json').exists():raise RuntimeError('Staging journal appeared during freeze')
        backup_sqlite(DB,backup/'clients.db')
        after_base,_,after_live=baseline(datetime.now(timezone.utc))
        if CONFIG.read_bytes()!=before_config or after_live!=live or after_base!=base:raise RuntimeError('Original state changed during preparation')
        for path in [DB,*[Path(str(DB)+suffix) for suffix in ('-wal','-shm')]]:
            if path.exists():path.chmod(0o600)
        write(STATE/'activation.json',json.dumps(dict(approved=True,original_clients=59,owner_id=owner,release=COMMIT,backup=str(backup))).encode())
        run('systemctl','daemon-reload')
        for old in OLD:run('systemctl','disable',old)
        for name in NEW:run('systemctl','enable','--now',name)
        bridge=health('http://127.0.0.1:5055/api/managed/health',token,require_scheduler=True)
        api=health('http://127.0.0.1:5056/health')
        if not bridge.get('scheduler_ok') or api.get('managed_test_only') is not False or not api.get('profile_delivery'):raise RuntimeError('Production capabilities not verified')
        if CONFIG.read_bytes()!=before_config or AWGRuntime().peers()!=live:raise RuntimeError('Activation changed VPN state')
        route_stage.SNIPPET=Path('/etc/caddy/vpn-manager-live.caddy')
        route_stage.IMPORT='    import /etc/caddy/vpn-manager-live.caddy'
        route_stage.ROUTES='handle_path /panel-live/* {\n root * /opt/vpn-manager-production/public\n file_server\n}\nhandle_path /live-api/* {\n reverse_proxy 127.0.0.1:5056\n}\n'
        route_stage.main()
        if CONFIG.read_bytes()!=before_config or AWGRuntime().peers()!=live:raise RuntimeError('Post-routing VPN invariant failed')
    except Exception:
        rollback(backup)
        raise RuntimeError('Activation failed; previous API routing restored. Inspect '+str(backup)) from None
    write(STATE/'rollback-command.txt',('python3 '+str(Path(__file__).resolve())+' --rollback '+str(backup)+'\n').encode())
    print('LIVE_MANAGER_READY; original_clients=59; initial_expiry_holds=5; VPN and Xray were not restarted')
    print('Panel: https://185-23-239-142.sslip.io:8443/panel-live/')
    print('Code-only rollback: python3 '+str(Path(__file__).resolve())+' --rollback '+str(backup))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--activate-approved',action='store_true');parser.add_argument('--rollback',type=Path)
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('Root required')
    os.umask(0o077)
    if args.rollback:return rollback(args.rollback)
    admin=preflight();materials=materialize()
    with tempfile.TemporaryDirectory(prefix='vpn-live-source-') as temp:
        source=Path(temp)
        for path,data in materials.items():
            if path.endswith('.py'):write(source/Path(path).name,data)
        sys.path.insert(0,str(source))
        import route_stage
        route_stage.adapt(Path('/etc/caddy/Caddyfile'))
        base,_,_=baseline(datetime.now(timezone.utc))
        if not args.activate_approved:
            print('LIVE_PREFLIGHT_READY; original_clients=59; initial_expiry_holds='+str(len(base['initial_expiry_hold']))+'; no activation performed')
            return
        activate(materials,admin)


if __name__=='__main__':
    try:main()
    except Exception as error:
        detail=str(error) if isinstance(error,RuntimeError) else type(error).__name__
        raise SystemExit('Promotion stopped: '+detail+'. Inspect the private backup if activation began.') from None
