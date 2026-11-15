#!/usr/bin/env python3
import copy, json, os, pathlib, shutil, subprocess, sys, uuid, urllib.parse

XRAY = pathlib.Path('/usr/local/etc/xray/config.json')
CADDY = pathlib.Path('/etc/caddy/Caddyfile')
SITE = pathlib.Path('/etc/caddy/vless-ws-test.caddy')
DOMAIN = 'connect.caesarbot.ru'
PUBLIC_IP = '185.23.239.142'
PORT = 8445
LOCAL_PORT = 10085
WS_PATH = '/vless-test-ws'

def run(args, **kw):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kw)

def restore(xray_bytes, caddy_bytes):
    XRAY.write_bytes(xray_bytes)
    CADDY.write_bytes(caddy_bytes)
    SITE.unlink(missing_ok=True)

if os.geteuid() != 0:
    sys.exit('Run as root')
if SITE.exists():
    sys.exit('STOP: test Caddy site already exists; no changes made')
old_xray = XRAY.read_bytes()
old_caddy = CADDY.read_bytes()
config = json.loads(old_xray)
reality = [x for x in config.get('inbounds', []) if x.get('protocol') == 'vless' and x.get('streamSettings', {}).get('security') == 'reality' and x.get('port') == 443]
if len(reality) != 1:
    sys.exit('STOP: expected exactly one VLESS REALITY inbound on port 443')
settings = reality[0]['streamSettings']['realitySettings']
if not settings.get('privateKey') or not settings.get('serverNames') or not settings.get('shortIds'):
    sys.exit('STOP: REALITY configuration incomplete')
if any(x.get('port') == LOCAL_PORT for x in config['inbounds']):
    sys.exit('STOP: local test port already used by Xray')
try:
    sockets = run(['ss', '-ltn']).stdout
    if any(':'+str(p)+' ' in line for line in sockets.splitlines() for p in [PORT, LOCAL_PORT]):
        sys.exit('STOP: requested port is occupied')
except subprocess.CalledProcessError:
    sys.exit('STOP: cannot check listening ports')
try:
    pub_result = run(['/usr/local/bin/xray', 'x25519', '-i', settings['privateKey']]).stdout
except subprocess.CalledProcessError:
    sys.exit('STOP: cannot derive REALITY public key')
public_key = None
for line in pub_result.splitlines():
    if ':' in line:
        k, v = line.split(':', 1)
        if k.strip().lower() in ('publickey', 'password'):
            public_key = v.strip()
if not public_key:
    sys.exit('STOP: REALITY public key not found; no changes made')
real_uuid = str(uuid.uuid4())
ws_uuid = str(uuid.uuid4())
reality[0]['settings']['clients'].append({'id': real_uuid, 'flow': 'xtls-rprx-vision', 'email': 'test-reality-20261115'})
config['inbounds'].append({
    'tag': 'vless-ws-test', 'listen': '127.0.0.1', 'port': LOCAL_PORT,
    'protocol': 'vless', 'settings': {'clients': [{'id': ws_uuid, 'email': 'test-ws-20261115'}], 'decryption': 'none'},
    'streamSettings': {'network': 'ws', 'security': 'none', 'wsSettings': {'path': WS_PATH}}
})
new_caddy = old_caddy.decode().rstrip() + '\n\nimport /etc/caddy/vless-ws-test.caddy\n'
site_text = f'''https://{DOMAIN}:{PORT} {{
    @vless path {WS_PATH}
    handle @vless {{
        reverse_proxy 127.0.0.1:{LOCAL_PORT}
    }}
    handle {{
        respond 404
    }}
}}
'''
# Validate prospective files without changing active configuration.
probe_xray = XRAY.with_name('config.vless-test-check.json')
probe_caddy = CADDY.with_name('Caddyfile.vless-test-check')
try:
    probe_xray.write_text(json.dumps(config, indent=2) + '\n')
    probe_caddy.write_text(new_caddy)
    SITE.write_text(site_text)
    run(['/usr/local/bin/xray', 'run', '-test', '-config', str(probe_xray)])
    run(['caddy', 'validate', '--config', str(probe_caddy), '--adapter', 'caddyfile'])
except Exception as exc:
    SITE.unlink(missing_ok=True)
    sys.exit(f'VALIDATION FAILED; original configs untouched: {exc}')
finally:
    probe_xray.unlink(missing_ok=True)
    probe_caddy.unlink(missing_ok=True)

XRAY.write_text(json.dumps(config, indent=2) + '\n')
CADDY.write_text(new_caddy)
try:
    run(['systemctl', 'restart', 'xray'])
    run(['systemctl', 'reload', 'caddy'])
    if run(['systemctl', 'is-active', 'xray']).stdout.strip() != 'active':
        raise RuntimeError('Xray not active')
    if run(['systemctl', 'is-active', 'caddy']).stdout.strip() != 'active':
        raise RuntimeError('Caddy not active')
except Exception as exc:
    restore(old_xray, old_caddy)
    subprocess.run(['systemctl', 'restart', 'xray'])
    subprocess.run(['systemctl', 'reload', 'caddy'])
    sys.exit(f'APPLY FAILED, rolled back: {exc}')

sni = settings['serverNames'][0]
sid = settings['shortIds'][0]
real_link = f'vless://{real_uuid}@{PUBLIC_IP}:443?security=reality&encryption=none&pbk={urllib.parse.quote(public_key)}&fp=chrome&sni={urllib.parse.quote(sni)}&sid={sid}&type=tcp&flow=xtls-rprx-vision#AdminVPS-REALITY-TEST'
ws_link = f'vless://{ws_uuid}@{DOMAIN}:{PORT}?security=tls&encryption=none&sni={DOMAIN}&type=ws&host={DOMAIN}&path={urllib.parse.quote(WS_PATH, safe="")}#AdminVPS-TLS-WS-TEST'
print('\nSUCCESS: two test profiles configured')
print('\nREALITY LINK:\n'+real_link)
print('\nTLS WS LINK:\n'+ws_link)
print('\nDo not publish these links. Check port 8445 from your network.')
