"""Only registration point for SQLite CRUD; subscription operations never call VPN."""
from calendar import monthrange
from datetime import date,timedelta
from flask import jsonify,request
PERIODS={'trial':0,'14d':0,'1m':1,'1':1,'2m':2,'6m':6,'6':6,'12m':12,'12':12,'lifetime':0,'imported':0}
def end_date(base,period):
 if period=='lifetime':return None
 if period in ('trial','14d'):return (base+timedelta(days=1 if period=='trial' else 14)).isoformat()
 months=PERIODS[period]
 if not months:raise ValueError()
 year,month=divmod(base.year*12+base.month-1+months,12);month+=1
 return date(year,month,min(base.day,monthrange(year,month)[1])).isoformat()
def register(app,authorized,db_connect,managed=None):
 def history(con,row,event,description):con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',(row['user_id'],row['id'],event,description))
 @app.get('/api/clients/<int:client_id>/history')
 @authorized
 def client_history(client_id):
  if app.config.get('MANAGED_AWG') and managed:return managed('/api/managed/clients/'+str(client_id)+'/history',method='GET')
  with db_connect() as con:rows=con.execute('SELECT event_type,description,created_at FROM client_history WHERE client_id=? ORDER BY created_at DESC,id DESC',(client_id,)).fetchall()
  return jsonify(ok=True,history=[dict(r) for r in rows])
 @app.patch('/api/clients/<int:client_id>')
 @authorized
 def edit(client_id):
  if app.config.get('MANAGED_AWG'):return jsonify(ok=False,error='Managed editing is not installed'),503
  d=request.get_json(silent=True)
  if not isinstance(d,dict) or not d or set(d)-{'name','phone','note','start_date','end_date','period','referrer_id'}:return jsonify(ok=False,error='Invalid fields'),400
  try:
   with db_connect() as con:
    con.execute('BEGIN IMMEDIATE');row=con.execute('SELECT * FROM clients WHERE id=?',(client_id,)).fetchone()
    if row is None:return jsonify(ok=False,error='Client not found'),404
    merged=dict(row);merged.update(d)
    if not isinstance(merged['name'],str) or not merged['name'].strip() or len(merged['name'])>150 or merged['period'] not in PERIODS:raise ValueError()
    date.fromisoformat(merged['start_date'])
    if merged['period']=='lifetime':d['end_date']=None
    else:date.fromisoformat(merged['end_date'])
    ref=d.get('referrer_id')
    if ref is not None and (type(ref)!=int or ref==client_id or not con.execute('SELECT 1 FROM clients WHERE id=?',(ref,)).fetchone()):raise ValueError()
    if len(str(merged.get('phone') or ''))>100 or len(str(merged.get('note') or ''))>2000:raise ValueError()
    con.execute('UPDATE clients SET '+','.join(k+'=?' for k in d)+' WHERE id=?',(*d.values(),client_id));history(con,row,'edited','Client details updated')
   return jsonify(ok=True,id=client_id,wireguard_unchanged=True)
  except (ValueError,TypeError):return jsonify(ok=False,error='Invalid client data'),400
 @app.post('/api/clients/<int:client_id>/extend')
 @authorized
 def extend(client_id):
  if app.config.get('MANAGED_AWG'):return jsonify(ok=False,error='Managed renewal is not installed'),503
  d=request.get_json(silent=True);period=d.get('period') if isinstance(d,dict) else None
  if not isinstance(period,str) or period not in PERIODS or period=='imported':return jsonify(ok=False,error='Invalid period'),400
  with db_connect() as con:
   con.execute('BEGIN IMMEDIATE');row=con.execute('SELECT * FROM clients WHERE id=?',(client_id,)).fetchone()
   if row is None:return jsonify(ok=False,error='Client not found'),404
   if row['period']=='lifetime' and period!='lifetime':return jsonify(ok=False,error='Lifetime cannot be shortened by renewal'),409
   base=max(date.today(),date.fromisoformat(row['end_date'][:10])) if row['end_date'] and row['period']!='lifetime' else date.today();end=end_date(base,period)
   con.execute('UPDATE clients SET period=?,end_date=? WHERE id=?',(period,end,client_id));history(con,row,'extended','Subscription renewed: '+period)
  return jsonify(ok=True,id=client_id,end_date=end,wireguard_unchanged=True)
 @app.post('/api/clients/create')
 @authorized
 def create():
  if app.config.get('MANAGED_AWG'):return jsonify(ok=False,error='Use atomic client provisioning'),409
  d=request.get_json(silent=True)
  try:
   name=d['name'].strip();start=date.fromisoformat(d['start_date']);period=d['period'];end=end_date(start,period);ref=int(d['referrer_id']) if d.get('referrer_id') else None
   if not name or len(name)>150 or len(str(d.get('phone','')))>100:raise ValueError()
  except (KeyError,TypeError,ValueError,AttributeError):return jsonify(ok=False,error='Invalid client data'),400
  with db_connect() as con:
   con.execute('BEGIN IMMEDIATE');owner=con.execute('SELECT user_id FROM clients LIMIT 1').fetchone()
   if owner is None:return jsonify(ok=False,error='Existing database owner required'),409
   if ref is not None and not con.execute('SELECT 1 FROM clients WHERE id=?',(ref,)).fetchone():return jsonify(ok=False,error='Referrer not found'),400
   cur=con.execute('INSERT INTO clients(user_id,name,phone,start_date,end_date,period,note,referrer_id) VALUES(?,?,?,?,?,?,?,?)',(owner['user_id'],name,d.get('phone',''),start.isoformat(),end,period,'',ref));row={'id':cur.lastrowid,'user_id':owner['user_id']};history(con,row,'created','Client created; VPN unchanged')
  return jsonify(ok=True,id=row['id']),201
 @app.post('/api/clients/<int:client_id>/delete')
 @authorized
 def delete(client_id):
  if app.config.get('MANAGED_AWG') and managed:return managed('/api/wg/client/'+str(client_id)+'/delete')
  with db_connect() as con:
   con.execute('BEGIN IMMEDIATE');row=con.execute('SELECT id,user_id,wg_client_id FROM clients WHERE id=?',(client_id,)).fetchone()
   if row is None:return jsonify(ok=False,error='Client not found'),404
   if row['wg_client_id']:return jsonify(ok=False,error='Linked AWG profile must be reviewed before deletion; client preserved'),409
   con.execute('CREATE TABLE IF NOT EXISTS awg_deleted_clients(client_id INTEGER PRIMARY KEY,deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
   con.execute('INSERT OR IGNORE INTO awg_deleted_clients(client_id) VALUES(?)',(client_id,))
   history(con,row,'deleted','Client archived; history preserved')
  return jsonify(ok=True,id=client_id,wireguard_unchanged=True)

