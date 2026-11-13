"""Personal client portal. No VPN-management capability or working database access."""
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib, hmac, json, math, os, re, secrets, sqlite3, subprocess, time
from cryptography.fernet import Fernet
from flask import Flask, Response, jsonify, request, send_file

ORIGIN='https://connect.caesarbot.ru:8444'
MOSCOW=timezone(timedelta(hours=3))
def stamp(value):
    if not value:return None
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    return parsed.replace(tzinfo=MOSCOW).timestamp() if parsed.tzinfo is None else parsed.timestamp()

def create_app(config=None):
    app=Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=4096,STATE='/var/lib/vpn-manager-connect/state',MATERIAL='/var/lib/vpn-manager-connect/material',ADMIN_TOKEN=os.environ.get('CONNECT_ADMIN_TOKEN',''),ORIGIN=ORIGIN)
    if config:app.config.update(config)
    state=Path(app.config['STATE']);material=Path(app.config['MATERIAL'])
    cipher=Fernet((state/'link.key').read_bytes());rates=defaultdict(deque)
    def db():
        con=sqlite3.connect(state/'links.db',timeout=15);con.row_factory=sqlite3.Row
        con.execute('PRAGMA busy_timeout=15000')
        con.execute('CREATE TABLE IF NOT EXISTS links(client_id INTEGER PRIMARY KEY,handle TEXT UNIQUE NOT NULL,token_hash TEXT NOT NULL,secret BLOB NOT NULL,active INTEGER NOT NULL,updated_at REAL NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS operations(request_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,result BLOB NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS link_events(id INTEGER PRIMARY KEY,client_id INTEGER NOT NULL,event TEXT NOT NULL,created_at REAL NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS portal_codes(client_id INTEGER PRIMARY KEY,salt TEXT NOT NULL,code_hash TEXT NOT NULL,secret BLOB NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS portal_sessions(session_hash TEXT PRIMARY KEY,client_id INTEGER NOT NULL,handle TEXT NOT NULL,expires REAL NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS portal_attempts(bucket TEXT PRIMARY KEY,started REAL NOT NULL,failures INTEGER NOT NULL)')
        return con
    def snapshot():
        data=json.loads((material/'portal.json').read_text())
        if data.get('version')!=2 or not 0<=time.time()-data.get('updated_at',0)<150:raise ValueError('Stale material')
        return data['clients']
    def new_code(con,cid):
        code=''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(12))
        salt=secrets.token_hex(16);digest=hashlib.pbkdf2_hmac('sha256',code.encode(),salt.encode(),150000).hex()
        con.execute('INSERT OR REPLACE INTO portal_codes VALUES(?,?,?,?)',(cid,salt,digest,cipher.encrypt(code.encode())))
        con.execute('DELETE FROM portal_sessions WHERE client_id=?',(cid,))
        return code
    def ensure(con,cid):
        row=con.execute('SELECT * FROM links WHERE client_id=?',(cid,)).fetchone()
        if not row:
            handle,token=secrets.token_urlsafe(16),secrets.token_urlsafe(32)
            con.execute('INSERT INTO links VALUES(?,?,?,?,1,?)',(cid,handle,hashlib.sha256(token.encode()).hexdigest(),cipher.encrypt(token.encode()),time.time()))
        if not con.execute('SELECT 1 FROM portal_codes WHERE client_id=?',(cid,)).fetchone():new_code(con,cid)
    def failure(message='Ссылка или код недействительны.',status=401):return jsonify(ok=False,error=message),status
    def credentials():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{22}',str(data.get('handle',''))) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',str(data.get('token',''))):return None
        with db() as con:row=con.execute('SELECT * FROM links WHERE handle=?',(data['handle'],)).fetchone()
        if not row or not row['active'] or not hmac.compare_digest(hashlib.sha256(data['token'].encode()).hexdigest(),row['token_hash']):return None
        return dict(row)
    def session(row):
        value=request.cookies.get('vpn_portal','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',value):return False
        with db() as con:found=con.execute('SELECT 1 FROM portal_sessions WHERE session_hash=? AND client_id=? AND handle=? AND expires>?',(hashlib.sha256(value.encode()).hexdigest(),row['client_id'],row['handle'],time.time())).fetchone()
        return bool(found)
    def authorized_profile():
        row=credentials()
        if not row or not session(row):return None,None
        current=snapshot().get(str(row['client_id']))
        return row,current
    def available(current):
        return bool(current and current.get('enabled') and (current.get('period')=='lifetime' or (stamp(current.get('end_date')) or 0)>time.time()))
    def info(current):
        lifetime=current.get('period')=='lifetime';end=None if lifetime else stamp(current.get('end_date'));start=stamp(current.get('start_date'))
        seconds=max(0,(end or 0)-time.time())
        progress=100 if lifetime else max(0,min(100,seconds/max(1,(end or 0)-(start or time.time()))*100))
        return dict(label='CAESAR VPN #'+str(current['number']),period=current.get('period'),start_date=current.get('start_date'),end_date=None if lifetime else current.get('end_date'),days_left=None if lifetime else math.ceil(seconds/86400),remaining_seconds=None if lifetime else int(seconds),progress=round(progress,1),enabled=bool(current.get('enabled')),available=available(current),lifetime=lifetime)
    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store, private','Pragma':'no-cache','X-Robots-Tag':'noindex, nofollow, noarchive, noimageindex','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})
        return response
    @app.before_request
    def guard():
        if request.path.startswith('/internal/'):
            token=app.config['ADMIN_TOKEN']
            if len(token)<32 or not hmac.compare_digest(token,request.headers.get('X-Connect-Admin','')):return failure(status=401)
        elif request.method=='POST':
            if request.headers.get('Origin')!=app.config['ORIGIN']:return failure(status=403)
            address=request.headers.get('X-Connect-IP',request.remote_addr or '')
            if len(rates)>2048:rates.clear()
            q=rates[address];now=time.time()
            while q and q[0]<now-60:q.popleft()
            if len(q)>=30:return failure('Слишком много запросов. Повторите позже.',429)
            q.append(now)
    @app.errorhandler(Exception)
    def error_handler(error):
        from werkzeug.exceptions import HTTPException
        return failure('Сервис временно недоступен.',error.code if isinstance(error,HTTPException) else 503)
    @app.get('/internal/status')
    def status():
        clients=snapshot()
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            for cid in clients:ensure(con,int(cid))
        return jsonify(ok=True,all_clients=True,client_ids=[int(x) for x in clients],code_required=True,import_mode='file')
    @app.post('/internal/link')
    def admin_link():
        data=request.get_json(silent=True);clients=snapshot()
        if not isinstance(data,dict) or set(data)!={'client_id','action'} or type(data['client_id']) is not int or str(data['client_id']) not in clients or data['action'] not in ('issue','rotate','revoke','reset-code'):return failure('Профиль пока недоступен. Повторите через минуту.',404)
        key=request.headers.get('Idempotency-Key','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',key):return failure('Stable idempotency key required',400)
        digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest();cid=data['client_id'];action=data['action']
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            previous=con.execute('SELECT * FROM operations WHERE request_id=?',(key,)).fetchone()
            if previous:
                if previous['payload_hash']!=digest:return failure('Idempotency key reused',409)
                return jsonify(json.loads(cipher.decrypt(previous['result'])))
            ensure(con,cid);row=con.execute('SELECT * FROM links WHERE client_id=?',(cid,)).fetchone()
            if action=='revoke':
                con.execute('UPDATE links SET active=0,updated_at=? WHERE client_id=?',(time.time(),cid));con.execute('DELETE FROM portal_sessions WHERE client_id=?',(cid,));result=dict(ok=True,revoked=True)
            else:
                if action=='issue' and not row['active']:return failure('Ссылка отозвана. Перевыпустите её явно.',409)
                if action=='rotate':
                    handle,token=secrets.token_urlsafe(16),secrets.token_urlsafe(32)
                    con.execute('UPDATE links SET handle=?,token_hash=?,secret=?,active=1,updated_at=? WHERE client_id=?',(handle,hashlib.sha256(token.encode()).hexdigest(),cipher.encrypt(token.encode()),time.time(),cid));con.execute('DELETE FROM portal_sessions WHERE client_id=?',(cid,))
                if action=='reset-code':new_code(con,cid)
                row=con.execute('SELECT * FROM links WHERE client_id=?',(cid,)).fetchone();code=con.execute('SELECT secret FROM portal_codes WHERE client_id=?',(cid,)).fetchone()[0]
                result=dict(ok=True,url=app.config['ORIGIN']+'/'+row['handle']+'#'+cipher.decrypt(row['secret']).decode(),access_code=cipher.decrypt(code).decode(),code_required=True,label=info(clients[str(cid)])['label'])
            con.execute('INSERT INTO operations VALUES(?,?,?)',(key,digest,cipher.encrypt(json.dumps(result).encode())))
            con.execute('INSERT INTO link_events(client_id,event,created_at) VALUES(?,?,?)',(cid,action,time.time()))
        return jsonify(result)
    @app.get('/')
    @app.get('/<handle>')
    def landing(handle=''):
        if handle and not re.fullmatch(r'[A-Za-z0-9_-]{22}',handle):return '',404
        return Response((Path(__file__).parent/'connect.html').read_text(),content_type='text/html; charset=utf-8')
    @app.get('/assets/<name>')
    def asset(name):
        if name not in ('connect.js','connect.css'):return '',404
        return send_file(Path(__file__).parent/name)
    @app.post('/api/login')
    def login():
        data=request.get_json(silent=True) or {};handle=str(data.get('handle',''))[:64]
        address=request.headers.get('X-Connect-IP',request.remote_addr or '')
        bucket=hashlib.sha256((handle+'|'+address).encode()).hexdigest();now=time.time()
        with db() as con:
            con.execute('BEGIN IMMEDIATE');con.execute('DELETE FROM portal_attempts WHERE started<?',(now-900,));con.execute('DELETE FROM portal_sessions WHERE expires<?',(now,))
            old=con.execute('SELECT failures FROM portal_attempts WHERE bucket=?',(bucket,)).fetchone()
            if old and old[0]>=5:return failure('Слишком много попыток. Повторите через 15 минут.',429)
            con.execute('INSERT INTO portal_attempts VALUES(?,?,1) ON CONFLICT(bucket) DO UPDATE SET failures=failures+1',(bucket,now))
        row=credentials();current=snapshot().get(str(row['client_id'])) if row else None
        code=re.sub(r'[\s-]','',str(data.get('code',''))).upper()
        with db() as con:
            saved=con.execute('SELECT * FROM portal_codes WHERE client_id=?',(row['client_id'] if row else -1,)).fetchone()
        salt=saved['salt'] if saved else '0'*32;candidate=hashlib.pbkdf2_hmac('sha256',code[:64].encode(),salt.encode(),150000).hex()
        if not saved or not current or not hmac.compare_digest(candidate,saved['code_hash']):return failure()
        value=secrets.token_urlsafe(32)
        with db() as con:
            con.execute('DELETE FROM portal_attempts WHERE bucket=?',(bucket,))
            con.execute('INSERT INTO portal_sessions VALUES(?,?,?,?)',(hashlib.sha256(value.encode()).hexdigest(),row['client_id'],row['handle'],now+43200))
        response=jsonify(ok=True,profile=info(current));response.set_cookie('vpn_portal',value,max_age=43200,secure=True,httponly=True,samesite='Strict',path='/')
        return response
    @app.post('/api/profile')
    def profile():
        row,current=authorized_profile()
        if not current:return failure()
        return jsonify(ok=True,profile=info(current))
    @app.post('/api/logout')
    def logout():
        with db() as con:con.execute('DELETE FROM portal_sessions WHERE session_hash=?',(hashlib.sha256(request.cookies.get('vpn_portal','').encode()).hexdigest(),))
        response=jsonify(ok=True);response.delete_cookie('vpn_portal',secure=True,httponly=True,samesite='Strict');return response
    @app.post('/api/download')
    @app.post('/api/qr')
    def download():
        row,current=authorized_profile()
        if not row or not current:return failure()
        if not available(current):return failure('Подписка истекла или доступ выключен. Обратитесь к администратору.',403)
        path=material/(str(row['client_id'])+'.conf')
        if path.is_symlink():return failure(status=404)
        contents=path.read_bytes()
        if hashlib.sha256(contents).hexdigest()!=current.get('sha256'):raise ValueError('Material changed')
        if request.path=='/api/qr':
            result=subprocess.run(['/usr/bin/qrencode','-t','SVG','-o','-'],input=contents,capture_output=True,timeout=10,check=True)
            return Response(result.stdout,content_type='image/svg+xml')
        return Response(contents,content_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="CAESAR-VPN-'+str(current['number'])+'.conf"'})
    return app

if __name__!='__main__' and os.environ.get('CONNECT_RUN')=='1':app=create_app()
