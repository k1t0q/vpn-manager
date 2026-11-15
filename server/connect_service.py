"""Isolated pilot profile delivery. No VPN token, runtime control or working DB access."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet
from flask import Flask, jsonify, request, Response, send_file

ORIGIN = 'https://connect.caesarbot.ru:8444'


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=2048, STATE='/var/lib/vpn-manager-connect/state',
                      MATERIAL='/var/lib/vpn-manager-connect/material',
                      ADMIN_TOKEN=os.environ.get('CONNECT_ADMIN_TOKEN', ''), ORIGIN=ORIGIN)
    if config:
        app.config.update(config)
    state = Path(app.config['STATE'])
    material = Path(app.config['MATERIAL'])
    key_path = state/'link.key'
    if not key_path.exists():
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(Fernet.generate_key()); out.flush(); os.fsync(out.fileno())
    cipher = Fernet(key_path.read_bytes())
    rates = defaultdict(deque)

    def db():
        con = sqlite3.connect(state/'links.db', timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA busy_timeout=15000')
        con.execute('CREATE TABLE IF NOT EXISTS links(client_id INTEGER PRIMARY KEY,handle TEXT UNIQUE NOT NULL,token_hash TEXT NOT NULL,secret BLOB NOT NULL,active INTEGER NOT NULL,updated_at REAL NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS operations(request_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,result BLOB NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS link_events(id INTEGER PRIMARY KEY,client_id INTEGER NOT NULL,event TEXT NOT NULL,created_at REAL NOT NULL)')
        return con

    def snapshot():
        data = json.loads((material/'status.json').read_text())
        if not isinstance(data.get('client_id'), int) or time.time() - data.get('updated_at', 0) > 150:
            raise ValueError('Snapshot stale')
        return data

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store, private', 'Pragma':'no-cache',
                                 'X-Robots-Tag':'noindex, nofollow, noarchive, noimageindex',
                                 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff',
                                 'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})
        return response

    @app.before_request
    def guard():
        if request.path.startswith('/internal/'):
            expected = app.config['ADMIN_TOKEN']
            if len(expected) < 32 or not hmac.compare_digest(request.headers.get('X-Connect-Admin', ''), expected):
                return jsonify(ok=False, error='Unauthorized'), 401
        elif request.method == 'POST':
            if request.headers.get('Origin') != app.config['ORIGIN']:
                return jsonify(ok=False, error='Origin forbidden'), 403
            address = request.headers.get('X-Connect-IP', request.remote_addr or '')
            now = time.time()
            if len(rates) > 2048:
                rates.clear()
            queue = rates[address]
            while queue and queue[0] < now - 60:
                queue.popleft()
            if len(queue) >= 30:
                return jsonify(ok=False, error='Повторите позже.'), 429
            queue.append(now)

    @app.errorhandler(Exception)
    def unavailable(error):
        # No traceback, URL fragments, body, keys, or configuration in logs.
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return jsonify(ok=False, error='Запрос недоступен.'), error.code
        return jsonify(ok=False, error='Сервис временно недоступен.'), 503

    @app.get('/internal/status')
    def status():
        data = snapshot()
        return jsonify(ok=True, client_id=data['client_id'], pilot_only=True, import_mode='file')

    @app.post('/internal/link')
    def link():
        data = request.get_json(silent=True)
        pilot = snapshot()
        if not isinstance(data, dict) or set(data) != {'client_id','action'} or data['client_id'] != pilot['client_id'] or data['action'] not in ('issue','rotate','revoke'):
            return jsonify(ok=False, error='Pilot client only'), 403
        request_id = request.headers.get('Idempotency-Key', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', request_id):
            return jsonify(ok=False, error='Stable idempotency key required'), 400
        digest = hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            previous = con.execute('SELECT * FROM operations WHERE request_id=?',(request_id,)).fetchone()
            if previous:
                if previous['payload_hash'] != digest:
                    return jsonify(ok=False,error='Idempotency key reused'),409
                return jsonify(json.loads(cipher.decrypt(previous['result'])))
            row = con.execute('SELECT * FROM links WHERE client_id=?',(data['client_id'],)).fetchone()
            action = data['action']
            if action == 'revoke':
                con.execute('UPDATE links SET active=0,updated_at=? WHERE client_id=?',(time.time(),data['client_id']))
                result = dict(ok=True,revoked=True)
            else:
                if row and not row['active'] and action == 'issue':
                    return jsonify(ok=False,error='Link revoked; explicitly reissue it'),409
                if row is None or action == 'rotate':
                    handle, token = secrets.token_urlsafe(16), secrets.token_urlsafe(32)
                    con.execute('INSERT INTO links VALUES(?,?,?,?,1,?) ON CONFLICT(client_id) DO UPDATE SET handle=excluded.handle,token_hash=excluded.token_hash,secret=excluded.secret,active=1,updated_at=excluded.updated_at',
                                (data['client_id'],handle,hashlib.sha256(token.encode()).hexdigest(),cipher.encrypt(token.encode()),time.time()))
                else:
                    handle, token = row['handle'], cipher.decrypt(row['secret']).decode()
                result = dict(ok=True,url=app.config['ORIGIN']+'/'+handle+'#'+token,pilot_only=True,import_mode='file')
            con.execute('INSERT INTO operations VALUES(?,?,?)',(request_id,digest,cipher.encrypt(json.dumps(result).encode())))
            con.execute('INSERT INTO link_events(client_id,event,created_at) VALUES(?,?,?)',(data['client_id'],action,time.time()))
        return jsonify(result)

    @app.get('/assets/<name>')
    def asset(name):
        if name not in ('connect.js','connect.css'):
            return '',404
        return send_file(Path(__file__).parent/name)

    @app.get('/')
    @app.get('/<handle>')
    def landing(handle=''):
        # The random path is only a selector. The credential lives after # and
        # never reaches HTTP request URLs, Caddy logs or link-preview crawlers.
        if handle and not re.fullmatch(r'[A-Za-z0-9_-]{22}',handle):
            return '',404
        return Response((Path(__file__).parent/'connect.html').read_text(),content_type='text/html; charset=utf-8')

    @app.post('/api/download')
    def download():
        data = request.get_json(silent=True)
        denied = lambda: (jsonify(ok=False,error='Ссылка недействительна или профиль недоступен.'),404)
        if not isinstance(data,dict) or set(data) != {'handle','token'} or not isinstance(data['token'],str) or not isinstance(data['handle'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',data['token']) or not re.fullmatch(r'[A-Za-z0-9_-]{22}',data['handle']):
            return denied()
        with db() as con:
            row = con.execute('SELECT * FROM links WHERE handle=?',(data['handle'],)).fetchone()
        if not row or not row['active'] or not hmac.compare_digest(hashlib.sha256(data['token'].encode()).hexdigest(),row['token_hash']):
            return denied()
        current = snapshot()
        if row['client_id'] != current['client_id'] or not current.get('available'):
            return denied()
        profile = material/'client.conf'
        if profile.is_symlink():
            return denied()
        contents = profile.read_bytes()
        if hashlib.sha256(contents).hexdigest() != current.get('sha256'):
            return jsonify(ok=False,error='Сервис временно недоступен.'),503
        return Response(contents,content_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="CAESAR-BOT.conf"'})

    return app


if __name__ != '__main__' and os.environ.get('CONNECT_RUN') == '1':
    app = create_app()
