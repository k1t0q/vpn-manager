"""Local status bridge. No profile download, mutation, shell, or startup writes."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

DB = Path('/opt/vpn-manager/data/clients-staging.db')
PROFILES = Path('/opt/vpn-manager/awg-clients')


def runtime():
    def show(field):
        return subprocess.run(['awg', 'show', 'awg-test', field], check=True,
                              capture_output=True, text=True, timeout=10).stdout
    peers = set(show('peers').split())
    handshakes = dict(line.split() for line in show('latest-handshakes').splitlines())
    transfers = {fields[0]: fields[1:] for fields in
                 (line.split() for line in show('transfer').splitlines())}
    return peers, handshakes, transfers


def collect(db=DB, profiles=PROFILES, read_runtime=runtime):
    peers, handshakes, transfers = read_runtime()
    with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=5) as con:
        deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
        rows = con.execute('SELECT id,wg_client_id,name FROM clients'+(' WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients)' if deleted else '')+' ORDER BY id').fetchall()
        table=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_profile_labels'").fetchone()
        labels={str(cid):'CAESAR BOT #'+str(number) for cid,number in con.execute('SELECT client_id,label_number FROM awg_profile_labels')} if table else {}
    result = []
    ids = set()
    public_keys = set()
    for cid, legacy_id, name in rows:
        if type(cid) is not int or cid <= 0:
            raise ValueError('Invalid client ID')
        folder = profiles/str(cid)
        path = folder/'metadata.json'
        if folder.is_symlink() or path.is_symlink():
            raise ValueError('Invalid metadata path')
        meta = json.loads(path.read_text())
        if str(meta['client_id']) != str(cid):
            raise ValueError('Metadata mapping mismatch')
        public = meta['public_key']
        if not re.fullmatch(r'[A-Za-z0-9+/]{43}=', public) or public in public_keys:
            raise ValueError('Invalid peer mapping')
        public_keys.add(public)
        # Preserve legacy database links without modifying the working database.
        link = str(legacy_id or cid)
        if link in ids:
            raise ValueError('Duplicate client link')
        ids.add(link)
        counters = transfers.get(public, ['0', '0'])
        if len(counters) != 2:
            raise ValueError('Invalid transfer response')
        result.append(dict(id=link, client_id=cid, name=str(name),
                           profile_name=labels.get(str(cid),meta.get('profile_name','CAESAR BOT #'+str(cid))),
                           address=str(ipaddress.ip_address(meta['address'])),
                           enabled=public in peers,
                           latestHandshake=int(handshakes.get(public, '0')),
                           transferRx=int(counters[0]), transferTx=int(counters[1])))
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log credentials, URLs, or profile contents.

    def reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        supplied = self.headers.get('X-VPN-Token', '')
        if not supplied or not hmac.compare_digest(supplied, self.server.token):
            return self.reply(401, dict(ok=False, error='Unauthorized'))
        if self.path != '/api/wg/clients':
            return self.reply(404, dict(ok=False, error='Read-only bridge'))
        try:
            data = collect()
        except Exception:
            return self.reply(503, dict(ok=False, error='AWG status unavailable'))
        return self.reply(200, dict(ok=True, clients=data))

    def do_POST(self):
        self.reply(405, dict(ok=False, error='Read-only bridge; VPN unchanged'))


if __name__ == '__main__':
    token = os.environ.get('PANEL_AWG_TOKEN', '')
    if len(token) < 32:
        raise SystemExit('Bridge token not configured')
    server = ThreadingHTTPServer(('127.0.0.1', 5054), Handler)
    server.token = token
    server.serve_forever()
