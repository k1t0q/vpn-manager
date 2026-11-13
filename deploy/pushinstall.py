"""Install owner-requested Web Push without changing VPN or client records."""
from datetime import datetime, timezone
import hashlib, json, os, pwd, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import Request, urlopen

RELEASE = '604b5e41ccf46e0e864757963d9a05d857eee9fc'
SITE = Path('/opt/vpn-manager-production')
ROOT = Path('/var/lib/vpn-manager-production')
AUTH = Path('/var/lib/vpn-manager-production-auth')
KEY = Path('/etc/vpn-manager-push/private.pem')
PYTHON = SITE/'venv/bin/python'
FILES = [
 ('server/production_app.py', '87f83cff45f7970b7eedb477c0bd1ca4961a4f67', '0543ad2fa1abf6c36c3f1ac865f75cc51cad5fad'),
 ('server/staging_api.py', 'eacf75a2cc2c64d0d7ca2aca3b163baa16f1eebf', 'a0860b04bed4d5b71402d643e0f8104a1e4c5cd3'),
 ('server/requirements.txt', None, 'f739462d4fb3c90dd94e6b9800346e8da3b224b1'),
 ('server/web_push.py', None, '98756d945bcaf73d0297d3f6410bfa441048ac69'),
 ('index.html', '89d8bf7bdf6d28bf20197b91aca7fd283bbf1d4e', 'bdc194d3455df250f2389100283bb6876be437a0'),
 ('sqlite-manager.js', '7738a0cf733bd34078852a89d046c5eadaa91bac', '57bc8665f184855eaae438289d153f70550bb308'),
 ('sw.js', 'd150aa3de37051d1ab5a05169a8a501f1cf31342', '2e4637c59d1e522840d7be1fcd9975379fe381ae'),
 ('manifest.webmanifest', 'bf547eb90e9714431f54660dd5edd78b968d8be0', 'd3da90c75a5178ee8b3ecb826404f3a835777fa7'),
 ('push-manager.js', None, 'e4d8892b46e421a74ad9f5bfe94bc19db34a978f'),
 ('push-icon-180.png', None, '01a3ca9b952784619fd1d108e9874029fbb150cd'),
 ('push-icon-192.png', None, '5a1d56475c70ce36e290d491292609b31bc2f389'),
 ('push-icon-512.png', None, 'bf38be56e00122129cb33979e1e9e6b4f2604303'),
]


def target(source):
    return SITE/source if source.startswith('server/') else SITE/'public'/source


def run(*args, timeout=60):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def blob(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def write(path, data, mode=0o644):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.web-push-')
    with os.fdopen(fd, 'wb') as out:
        os.fchmod(out.fileno(), mode)
        out.write(data); out.flush(); os.fsync(out.fileno())
    os.replace(name, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def backup_db(source, destination):
    with sqlite3.connect(destination) as dest:
        with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as con: con.backup(dest)
        assert dest.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    destination.chmod(0o600)


def rollback(backup):
    manifest = json.loads((backup/'manifest.json').read_text())
    subprocess.run(['systemctl','disable','--now','vpn-manager-push.timer'], capture_output=True)
    subprocess.run(['systemctl','stop','vpn-manager-push.service'], capture_output=True)
    run('systemctl','stop','vpn-manager-live-api.service')
    for item in manifest:
        path = Path(item['target'])
        if item['existed']:
            write(path, (backup/item['saved']).read_bytes(), item['mode'])
        # Keep new inert files and VAPID material for repeatable subscription recovery.
    run('systemctl','start','vpn-manager-live-api.service')
    print('CODE_ROLLBACK_READY; database, VPN state, and device key were not restored')


def main():
    if os.geteuid() != 0: raise SystemExit('Root required')
    os.umask(0o077)
    os.environ['NO_PROXY'] = '127.0.0.1,localhost'
    if len(sys.argv) == 3 and sys.argv[1] == '--rollback':
        backup = Path(sys.argv[2]).resolve()
        if backup.parent != Path('/root/vpn-manager-backups') or not backup.name.startswith('web-push-'): raise SystemExit('Invalid backup path')
        return rollback(backup)
    if (ROOT/'operations/pending.json').exists(): raise SystemExit('Pending operation; inspect first')
    if not json.loads((ROOT/'activation.json').read_text()).get('approved'): raise SystemExit('Production not approved')
    materials = {}
    for source, old, new in FILES:
        path = target(source)
        if path.is_symlink(): raise SystemExit('Symlink target rejected')
        if old is None:
            if path.exists() and blob(path.read_bytes()) != new: raise SystemExit('New target differs: '+source)
        elif not path.exists() or blob(path.read_bytes()) != old:
            raise SystemExit('Installed source differs; review first: '+source)
        with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source, timeout=30) as response: data = response.read()
        if blob(data) != new: raise SystemExit('Release digest mismatch: '+source)
        if source.endswith('.py'): compile(data,source,'exec')
        materials[source] = data
    unit = Path('/etc/systemd/system/vpn-manager-push.service')
    timer = Path('/etc/systemd/system/vpn-manager-push.timer')
    if unit.is_symlink() or timer.is_symlink(): raise SystemExit('Symlink service rejected')
    backup = Path('/root/vpn-manager-backups')/('web-push-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    manifest = []
    for number, (source, old, new) in enumerate(FILES):
        path = target(source)
        item = dict(target=str(path), existed=path.exists(), saved=str(number), mode=path.stat().st_mode & 0o777 if path.exists() else 0o644)
        if path.exists(): shutil.copy2(path,backup/str(number))
        manifest.append(item)
    (backup/'manifest.json').write_text(json.dumps(manifest))
    backup_db(AUTH/'sessions.db',backup/'sessions.db')
    backup_db(Path('/opt/vpn-manager/data/clients-staging.db'),backup/'clients.db')
    config = Path('/etc/amnezia/amneziawg/awg-test.conf')
    before = config.read_bytes(); (backup/'awg-test.conf').write_bytes(before)
    peers = set(run('awg','show','awg-test','peers').split())
    xray = run('systemctl','show','xray.service','-p','MainPID','--value')
    bridge = run('systemctl','show','vpn-manager-live-bridge.service','-p','MainPID','--value')
    # Add only the push dependency; no upgrade of the existing Flask/API stack.
    run(PYTHON,'-m','pip','install','pywebpush>=2.1,<3',timeout=240)
    # pip ran under the private backup umask; package code must remain readable
    # by vpn-panel. This touches only root-owned library files, never credentials.
    libraries = Path(run(PYTHON,'-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'))
    for directory, folders, filenames in os.walk(libraries, followlinks=False):
        for library in [Path(directory)]+[Path(directory)/name for name in filenames]:
            if library.is_symlink() or library.stat().st_uid != 0: continue
            library.chmod((library.stat().st_mode & 0o777) | (0o555 if library.is_dir() else 0o444))
    run('runuser','-u','vpn-panel','--',PYTHON,'-c','import cryptography.hazmat.primitives.serialization;import pywebpush')
    account = pwd.getpwnam('vpn-panel')
    KEY.parent.mkdir(mode=0o750,exist_ok=True); os.chown(KEY.parent,0,account.pw_gid)
    if not KEY.exists():
        run(PYTHON,'-c',"from pathlib import Path;from cryptography.hazmat.primitives.asymmetric import ec;from cryptography.hazmat.primitives import serialization;k=ec.generate_private_key(ec.SECP256R1());Path('/etc/vpn-manager-push/private.pem').write_bytes(k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))")
    if KEY.is_symlink(): raise SystemExit('Symlink key rejected')
    KEY.chmod(0o640); os.chown(KEY,0,account.pw_gid)
    shutil.copy2(KEY,backup/'push-private.pem'); (backup/'push-private.pem').chmod(0o600)
    service = '''[Unit]
Description=VPN MANAGER Web Push reminders
After=network-online.target vpn-manager-live-bridge.service
[Service]
Type=oneshot
User=vpn-panel
WorkingDirectory=/opt/vpn-manager-production/server
EnvironmentFile=/etc/vpn-manager-live.env
Environment=NO_PROXY=127.0.0.1,localhost
ExecStart=/opt/vpn-manager-production/venv/bin/python -m web_push
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
CapabilityBoundingSet=
ReadWritePaths=/var/lib/vpn-manager-production-auth
TimeoutStartSec=180
'''
    timer_text = '''[Unit]
Description=VPN MANAGER reminder delivery every fifteen minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
AccuracySec=30s
Unit=vpn-manager-push.service
[Install]
WantedBy=timers.target
'''
    for path, expected in [(unit, service), (timer, timer_text)]:
        if path.exists() and path.read_text() != expected: raise SystemExit('Installed timer source differs')
    run('systemctl','stop','vpn-manager-live-api.service')
    phase = 'install-api'
    try:
        for source, old, new in FILES: write(target(source),materials[source])
        write(unit,service.encode()); write(timer,timer_text.encode())
        run('systemctl','daemon-reload')
        run('systemctl','start','vpn-manager-live-api.service')
        phase = 'api-login-health'
        env = dict(line.split('=',1) for line in Path('/etc/vpn-manager-live.env').read_text().splitlines() if '=' in line)
        for _ in range(30):
            try:
                req = Request('http://127.0.0.1:5056/auth/login',data=json.dumps({'code':env['PANEL_ADMIN_CODE']}).encode(),headers={'Content-Type':'application/json'})
                with urlopen(req,timeout=3) as response: token = json.load(response)['access_token']
                break
            except OSError: time.sleep(1)
        else: raise RuntimeError('API login health failed')
        phase = 'push-key-health'
        headers = {'Authorization':'Bearer '+token}
        with urlopen(Request('http://127.0.0.1:5056/api/push/key',headers=headers),timeout=5) as response: result = json.load(response)
        assert result['ok'] and len(result['public_key']) == 87
        with urlopen('http://127.0.0.1:5056/health',timeout=5) as response: health = json.load(response)
        assert health['notification_delivery']=='web-push' and health['scheduler_ok']
        with urlopen(Request('http://127.0.0.1:5056/auth/logout',data=b'{}',headers=dict(headers,**{'Content-Type':'application/json'})),timeout=5): pass
        phase = 'push-worker-start'
        run('systemctl','start','vpn-manager-push.service',timeout=200)
        run('systemctl','enable','--now','vpn-manager-push.timer')
        assert run('systemctl','is-active','vpn-manager-push.timer') == 'active'
        assert config.read_bytes() == before
        assert set(run('awg','show','awg-test','peers').split()) == peers
        assert run('systemctl','show','xray.service','-p','MainPID','--value') == xray
        assert run('systemctl','show','vpn-manager-live-bridge.service','-p','MainPID','--value') == bridge
        assert b'next_tick=time.monotonic()+86400' in Path('/usr/local/libexec/vpn-manager-production/awg_production_server.py').read_bytes()
    except Exception as error:
        rollback(backup)
        print(json.dumps(dict(failed_phase=phase,error_type=type(error).__name__,backup=str(backup))))
        raise SystemExit('Push update rolled back to previous API code. Inspect private backup.') from None
    report = dict(ok=True,version='1.75',push_timer='active',auto_expiry_interval=86400,vpn_unchanged=True,backup=str(backup),device_confirmation_required=True)
    (ROOT/'web-push-release.json').write_text(json.dumps(dict(report,release=RELEASE)))
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
