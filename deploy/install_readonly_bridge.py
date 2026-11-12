"""Owner-run installer: local status only; never starts or changes AWG/Xray."""
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.request import urlopen

SOURCE_SHA = '74ef395cd24a7bd424f66945ea656e0a611f7b8a35d7b97c9521a98383bb06b5'
SOURCE_COMMIT = '3d67be97cda93d775ce5853a7612160ea2d2d5ad'


def write(path, data, mode):
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def main():
    if os.geteuid() != 0: raise SystemExit('Root required')
    env = Path('/etc/vpn-manager-stage.env')
    before = env.read_text()
    lines = before.splitlines()
    if 'PANEL_AWG_TOKEN=' not in lines:
        raise SystemExit('Existing AWG connection detected; nothing changed')
    data = urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+SOURCE_COMMIT+
                   '/deploy/awg_readonly_bridge.py', timeout=30).read()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA:
        raise SystemExit('Source digest mismatch; nothing changed')
    backup = Path('/root/vpn-manager-backups')/('readonly-bridge-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(env, backup/'stage.env')
    token = secrets.token_urlsafe(48)
    bridge_env = Path('/etc/vpn-manager-awg-readonly.env')
    if bridge_env.exists(): raise SystemExit('Bridge already installed; inspect first')
    write('/usr/local/libexec/vpn-manager-awg-readonly.py', data, 0o600)
    write(bridge_env, ('PANEL_AWG_TOKEN='+token+'\n').encode(), 0o600)
    unit = '''[Unit]
Description=VPN Manager local read-only AWG status
After=network.target
[Service]
Type=simple
User=root
EnvironmentFile=/etc/vpn-manager-awg-readonly.env
ExecStart=/usr/bin/python3 /usr/local/libexec/vpn-manager-awg-readonly.py
Restart=on-failure
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK
CapabilityBoundingSet=CAP_NET_ADMIN CAP_DAC_READ_SEARCH
[Install]
WantedBy=multi-user.target
'''
    write('/etc/systemd/system/vpn-manager-awg-readonly.service', unit.encode(), 0o644)
    lines=[('PANEL_AWG_TOKEN='+token) if line=='PANEL_AWG_TOKEN=' else
           'PANEL_AWG_URL=http://127.0.0.1:5054' if line.startswith('PANEL_AWG_URL=') else line
           for line in lines]
    write(env, ('\n'.join(lines)+'\n').encode(), 0o600)
    try:
        for command in [['systemctl','daemon-reload'],
                        ['systemctl','enable','--now','vpn-manager-awg-readonly.service'],
                        ['systemctl','restart','vpn-manager-stage.service']]:
            subprocess.run(command, check=True, capture_output=True, timeout=30)
        from urllib.request import Request
        import json
        request=Request('http://127.0.0.1:5054/api/wg/clients',headers={'X-VPN-Token':token})
        import time
        for attempt in range(5):
            try:
                with urlopen(request,timeout=15) as response: count=len(json.load(response)['clients'])
                break
            except OSError:
                if attempt==4:raise
                time.sleep(1)
        print('READONLY_AWG_READY; clients='+str(count)+'; VPN unchanged')
    except Exception:
        write(env,before.encode(),0o600)
        subprocess.run(['systemctl','disable','--now','vpn-manager-awg-readonly.service'],capture_output=True)
        subprocess.run(['systemctl','restart','vpn-manager-stage.service'],capture_output=True)
        raise SystemExit('Bridge setup failed; preview connection restored. Backup: '+str(backup)) from None


if __name__=='__main__':main()
