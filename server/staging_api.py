"""Isolated panel service. Never imports the obsolete app.py or changes VPN at startup."""
import re
import functools,hashlib,hmac,os,secrets,sqlite3,time
from flask import Flask,jsonify,request,Response
import requests

def create_app(config=None):
 app=Flask(__name__)
 app.config["MAX_CONTENT_LENGTH"]=16384
 app.config.update(DB_PATH=os.environ.get('VPN_MANAGER_DB','/opt/vpn-manager/data/clients-staging.db'),ADMIN_CODE=os.environ.get('PANEL_ADMIN_CODE',''),AWG_URL=os.environ.get('PANEL_AWG_URL','http://127.0.0.1:5000'),AWG_TOKEN=os.environ.get('PANEL_AWG_TOKEN',''),ORIGIN=os.environ.get('PANEL_ORIGIN','https://k1t0q.github.io'))
 if config:app.config.update(config)
 app.config.setdefault('MANAGED_AWG',os.environ.get('PANEL_MANAGED_AWG','')=='test-only')
 attempts={}
 def db_connect():
  con=sqlite3.connect('file:'+app.config['DB_PATH']+'?mode=rw',uri=True,timeout=15);con.row_factory=sqlite3.Row;con.execute('PRAGMA foreign_keys=ON');return con
 def authorized(fn):
  @functools.wraps(fn)
  def wrapped(*a,**kw):
   token=request.headers.get('Authorization','').removeprefix('Bearer ')
   try:
    with db_connect() as con:row=con.execute('SELECT expires_at FROM panel_sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
   except sqlite3.OperationalError:row=None
   if not token or row is None or row['expires_at']<=time.time():return jsonify(ok=False,error='Unauthorized'),401
   return fn(*a,**kw)
  return wrapped
 @app.before_request
 def preflight():
  if request.headers.get('Origin') not in (None,app.config['ORIGIN']):return jsonify(ok=False,error='Origin forbidden'),403
  if request.method=='OPTIONS':return '',204
 @app.after_request
 def headers(r):
  r.headers.update({'Access-Control-Allow-Origin':app.config['ORIGIN'],'Access-Control-Allow-Headers':'Authorization, Content-Type, Idempotency-Key','Access-Control-Allow-Methods':'GET, POST, PATCH, DELETE, OPTIONS','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});return r
 @app.errorhandler(sqlite3.Error)
 def db_error(e):return jsonify(ok=False,error='Database operation failed'),503
 @app.get('/health')
 def health():
  capabilities={}
  if app.config['MANAGED_AWG'] and app.config['AWG_TOKEN']:
   try:
    result=requests.get(app.config['AWG_URL'].rstrip('/')+'/api/managed/health',headers={'X-VPN-Token':app.config['AWG_TOKEN']},timeout=2)
    if result.ok:capabilities=result.json()
   except (requests.RequestException,ValueError):pass
  return jsonify(ok=True,version='sqlite-awg-panel-2',auto_expiry=bool(capabilities.get('auto_expiry')),subscriptions=bool(capabilities.get('subscriptions')),scheduler_ok=bool(capabilities.get('scheduler_ok')),notification_delivery='web-push' if app.config.get('WEB_PUSH') else capabilities.get('notification_delivery','unconfigured'),login_configured=bool(app.config['ADMIN_CODE']),awg_bridge_configured=bool(app.config['AWG_TOKEN']),managed_test_only=bool(capabilities.get('managed_test_only',True)),profile_delivery=bool(capabilities.get('profile_delivery')),profile_import=bool(capabilities.get('profile_import')),read_only=bool(capabilities.get('read_only')))
 @app.post('/auth/login')
 def login():
  now=time.time();key=request.remote_addr;recent=[t for t in attempts.get(key,[]) if t>now-300];attempts[key]=recent
  if len(recent)>=10:return jsonify(ok=False,error='Too many attempts'),429
  recent.append(now);d=request.get_json(silent=True);code=d.get('code','') if isinstance(d,dict) else ''
  if not app.config['ADMIN_CODE'] or not isinstance(code,str) or not hmac.compare_digest(code,app.config['ADMIN_CODE']):return jsonify(ok=False,error='Unauthorized'),401
  token=secrets.token_urlsafe(32)
  with db_connect() as con:
   con.execute('CREATE TABLE IF NOT EXISTS panel_sessions(token_hash TEXT PRIMARY KEY,expires_at REAL NOT NULL)')
   con.execute('DELETE FROM panel_sessions WHERE expires_at<=?',(now,));con.execute('INSERT INTO panel_sessions VALUES(?,?)',(hashlib.sha256(token.encode()).hexdigest(),now+43200))
  return jsonify(ok=True,access_token=token,expires_in=43200)
 @app.post('/auth/logout')
 @authorized
 def logout():
  with db_connect() as con:con.execute('DELETE FROM panel_sessions WHERE token_hash=?',(hashlib.sha256(request.headers['Authorization'].removeprefix('Bearer ').encode()).hexdigest(),))
  return jsonify(ok=True)
 @app.get('/api/clients')
 @authorized
 def clients():
  if app.config['MANAGED_AWG']:return managed('/api/managed/clients',method='GET')
  with db_connect() as con:
   deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
   rows=con.execute('SELECT id,name,phone,start_date,end_date,period,note,created_at,referrer_id,wg_client_id FROM clients'+(' WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients)' if deleted else '')+' ORDER BY id').fetchall()
  return jsonify(ok=True,clients=[dict(r) for r in rows])
 def managed(path,method='POST'):
  if not app.config['MANAGED_AWG'] or not app.config['AWG_TOKEN']:
   return jsonify(ok=False,error='Test peer management is not installed; no client created'),503
  key=request.headers.get('Idempotency-Key','')
  if method=='POST' and not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',key):return jsonify(ok=False,error='Stable idempotency key required'),400
  try:
   if method=='GET':r=requests.get(app.config['AWG_URL'].rstrip('/')+path,headers={'X-VPN-Token':app.config['AWG_TOKEN']},timeout=20)
   else:r=requests.post(app.config['AWG_URL'].rstrip('/')+path,headers={'X-VPN-Token':app.config['AWG_TOKEN'],'Idempotency-Key':key},json=request.get_json(silent=True) or {},timeout=30)
  except requests.RequestException:return jsonify(ok=False,error='Result unknown; retry the same request without changing its key'),503
  return Response(r.content,status=r.status_code,content_type='application/json')
 @app.post('/api/clients/provision')
 @authorized
 def provision():return managed('/api/managed/create')
 @app.post('/api/clients/<int:client_id>/referral')
 @authorized
 def managed_referral(client_id):return managed('/api/managed/clients/'+str(client_id)+'/referral')
 @app.get('/api/notifications')
 @authorized
 def notifications():return managed('/api/managed/notifications',method='GET')
 @app.post('/api/clients/<int:client_id>/delivery')
 @authorized
 def profile_delivery(client_id):return managed('/api/managed/clients/'+str(client_id)+'/delivery')
 @app.route('/api/clients/import',methods=['GET','POST'])
 @authorized
 def profile_import():return managed('/api/managed/import',method=request.method)
 from sqlite_client_crud import register
 register(app,authorized,db_connect,managed)
 from sqlite_referral_safe import register
 register(app,authorized,db_connect)
 from sqlite_wg_link import register
 register(app,authorized,db_connect)
 # Do not register sqlite_client_edit (duplicate PATCH), sqlite_client_delete
 # (wg-easy). AWG links are verified against the running service.
 @app.route('/api/wg/clients',methods=['GET'])
 @app.route('/api/wg/client',methods=['POST'])
 @app.route('/api/wg/client/<path:peer_path>',methods=['GET','POST'])
 @authorized
 def awg(peer_path=None):
  if peer_path:
   if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}/(qr|config|enable|disable)',peer_path):return jsonify(ok=False,error='Invalid peer ID'),400
   action=peer_path.rsplit('/',1)[-1]
   if action not in ('qr','config','enable','disable'):return jsonify(ok=False,error='Unsupported action'),404
   if (action in ('qr','config'))!=(request.method=='GET'):return jsonify(ok=False,error='Method not allowed'),405
  if request.method=='POST' and app.config['MANAGED_AWG']:
   if not peer_path:return jsonify(ok=False,error='Use atomic client provisioning'),409
   return managed(request.path)
  if not app.config['AWG_TOKEN']:return jsonify(ok=False,error='AWG bridge not configured'),503
  try:r=requests.request(request.method,app.config['AWG_URL'].rstrip('/')+request.path,headers={'X-VPN-Token':app.config['AWG_TOKEN']},json=request.get_json(silent=True) if request.method=='POST' else None,timeout=20)
  except requests.RequestException:return jsonify(ok=False,error='AWG unavailable; verify result before retrying'),503
  if not r.ok:return jsonify(ok=False,error='AWG request failed; verify profile before retrying'),r.status_code
  if request.path=='/api/wg/clients':
   try:
    peers=r.json()['clients']
    if not isinstance(peers,list):raise ValueError()
    fields={'id','client_id','profile_name','name','address','enabled','latestHandshakeAt','latestHandshake','latest_handshake_at','transferRx','transferTx'}
    return jsonify(ok=True,clients=[{k:v for k,v in peer.items() if k in fields} for peer in peers])
   except (ValueError,KeyError,AttributeError,TypeError):return jsonify(ok=False,error='Unexpected AWG list format'),502
  return Response(r.content,status=r.status_code,content_type=r.headers.get('Content-Type','application/octet-stream'))
 if app.config.get('WEB_PUSH'):
  from web_push import register as register_push
  register_push(app,authorized)
 return app
app=create_app()


