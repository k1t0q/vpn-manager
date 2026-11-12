"""Journaled subscriptions and expiry for explicitly permitted peers only."""
from datetime import date,datetime,timedelta,timezone
import hashlib,ipaddress,json,os,re,sqlite3,uuid
from awg_persistence import PeerStore,StateError,atomic_write,peer_blocks,sync_dir,subscription_deadline
from awg_provision import deadline,MONTHS,BONUS

PERIODS={*MONTHS,'14d','trial','lifetime','imported'}


class Subscriptions(PeerStore):
 def change(self,cid,kind,data,request_id,now=None):
  cid=str(cid);now=now or datetime.now(timezone.utc)
  if now.tzinfo is None:raise StateError('Timezone-aware clock required')
  if cid in self.protected:raise StateError('Existing client is protected from staging operations')
  if not isinstance(data,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',request_id):raise StateError('Invalid subscription request')
  payload=hashlib.sha256(json.dumps(['subscription',cid,kind,data],sort_keys=True).encode()).hexdigest()
  with self.locked():
   self._recover()
   with self.connect() as con:
    self._schema(con)
    old=con.execute('SELECT * FROM awg_operations WHERE request_id=?',(request_id,)).fetchone()
    if old:
     if old['payload_hash']!=payload:raise StateError('Idempotency key reused')
     return json.loads(old['result'])
    con.execute('BEGIN IMMEDIATE')
    deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
    if deleted and con.execute('SELECT 1 FROM awg_deleted_clients WHERE client_id=?',(cid,)).fetchone():raise StateError('Client is archived')
    row=con.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone()
    if row is None:raise StateError('Client not found')
    path=self._metadata(cid);before_meta=path.read_text();meta=json.loads(before_meta)
    if str(meta['client_id'])!=cid:raise StateError('Metadata client mismatch')
    public=meta['public_key'];address=str(ipaddress.ip_address(meta['address']))+'/32'
    before=self.config.read_text();header,blocks=peer_blocks(before)
    if re.search(r'(?im)^\s*SaveConfig\s*=\s*(true|yes|on|1)\s*$',header):raise StateError('SaveConfig must be disabled')
    matches=[(fields,block) for key,fields,block in blocks if key==public]
    before_block=matches[0][1] if matches else None
    if matches and matches[0][0].get('AllowedIPs')!=address:raise StateError('Client address mismatch')
    if (public in self.runtime.peers())!=bool(before_block):raise StateError('Target disk/runtime drift requires review')
    updates,event,description,notification=self._plan(con,row,meta,kind,data,now)
    updated=dict(row);updated.update(updates)
    end=subscription_deadline(updated['period'],updated['end_date'])
    after_block=before_block
    if kind=='expire':
     if before_block:meta['disabled_peer_block']=before_block
     meta['auto_disabled']=bool(before_block) or bool(meta.get('auto_disabled'))
     meta['disabled_reason']='expired' if meta['auto_disabled'] else 'manual'
     after_block=None
    elif end is not None and end<=now:
     if before_block:
      meta['disabled_peer_block']=before_block;meta['auto_disabled']=True;meta['disabled_reason']='expired'
     after_block=None
    elif meta.get('auto_disabled'):
     after_block=meta.get('disabled_peer_block')
     if not after_block:raise StateError('Original peer block unavailable')
     meta['auto_disabled']=False;meta['disabled_reason']=None
    if after_block:
     _,saved=peer_blocks(after_block)
     if len(saved)!=1 or saved[0][0]!=public or saved[0][1].get('AllowedIPs')!=address:raise StateError('Saved peer does not match profile')
    meta.update({key:updated[key] for key in ('name','period','end_date')})
    meta['enabled']=after_block is not None
    after_meta=json.dumps(meta,ensure_ascii=False,indent=2)
    after=before
    if after_block!=before_block:
     after=header+''.join(block for key,_,block in blocks if key!=public)
     if after_block:after=after.rstrip()+'\n\n'+after_block.rstrip()+'\n'
    backup=self.state/('op-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
    with sqlite3.connect(backup/'clients.db') as target:
     with sqlite3.connect(self.db_path) as source:source.backup(target)
    os.chmod(backup/'clients.db',0o600)
    journal=dict(request_id=request_id,cid=cid,public_key=public,before_block=before_block,after_block=after_block,
                 before_config=before,after_config=after,before_metadata=before_meta,after_metadata=after_meta)
    atomic_write(backup/'journal.json',json.dumps(journal).encode());atomic_write(self.state/'pending.json',json.dumps(journal).encode())
    self.fault('prepared')
    if after!=before:atomic_write(self.config,after.encode())
    self.fault('config_written');atomic_write(path,after_meta.encode());self.fault('metadata_written')
    if after_block!=before_block:self.runtime.apply(public,after_block)
    self.fault('runtime_applied')
    if updates:con.execute('UPDATE clients SET '+','.join(key+'=?' for key in updates)+' WHERE id=?',(*updates.values(),cid))
    con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',(row['user_id'],cid,event,description))
    if notification:self._notify(con,row,notification)
    result=dict(ok=True,id=cid,period=updated['period'],end_date=updated['end_date'],enabled=after_block is not None)
    con.execute('INSERT INTO awg_operations(request_id,payload_hash,result) VALUES(?,?,?)',(request_id,payload,json.dumps(result)))
    con.commit();self.fault('db_committed')
    (self.state/'pending.json').rename(backup/'committed.json');sync_dir(self.state)
    return result

 def _plan(self,con,row,meta,kind,data,now):
  notification=None
  if kind=='extend':
   if set(data)!={'period'} or data['period'] not in PERIODS-{'imported'}:raise StateError('Invalid renewal tariff')
   period=data['period']
   if row['period']=='lifetime':updates=dict(period='lifetime',end_date=None)
   elif period=='trial':updates=dict(period=period,end_date=deadline(now.date(),period,now))
   else:
    old=subscription_deadline(row['period'],row['end_date'])
    base=max(now.date(),old.date()) if old else now.date()
    updates=dict(period=period,end_date=deadline(base,period,now))
   return updates,'extended','Subscription renewed: '+updates['period'],None
  if kind=='edit':
   fields={'name','phone','note','start_date','end_date','period','referrer_id'}
   if not data or set(data)-fields:raise StateError('Invalid edit fields')
   updates=dict(data);merged=dict(row);merged.update(updates)
   if not isinstance(merged['name'],str) or not merged['name'].strip() or len(merged['name'])>150:raise StateError('Invalid name')
   for key,limit in [('phone',100),('note',2000)]:
    if merged.get(key) is not None and (not isinstance(merged[key],str) or len(merged[key])>limit):raise StateError('Invalid details')
   if merged['period'] not in PERIODS:raise StateError('Invalid tariff')
   try:date.fromisoformat(merged['start_date'])
   except (ValueError,TypeError):raise StateError('Invalid start date') from None
   if merged['period']=='lifetime':updates['end_date']=None
   else:
    if not merged['end_date']:raise StateError('Deadline required')
    subscription_deadline(merged['period'],merged['end_date'])
   ref=merged.get('referrer_id')
   if ref is not None:
    if type(ref) is not int or str(ref)==str(row['id']) or str(ref) in self.protected:raise StateError('Invalid staging referrer')
    if not con.execute('SELECT 1 FROM clients WHERE id=? AND user_id=?',(ref,row['user_id'])).fetchone():raise StateError('Referrer not found')
   return updates,'edited','Client details updated',None
  if kind=='expire':
   end=subscription_deadline(row['period'],row['end_date'])
   if data or end is None or end>now:raise StateError('Subscription has not expired')
   notification=('expired',row['end_date'])
   return {},'expired','Subscription expired; access disabled',notification
  if kind=='referral':
   if set(data)-{'client_id','bonus_period'} or type(data.get('client_id')) is not int:raise StateError('Invalid referral request')
   source=con.execute('SELECT * FROM clients WHERE id=?',(data['client_id'],)).fetchone()
   if source is None or source['referrer_id']!=row['id'] or source['user_id']!=row['user_id'] or str(source['id']) in self.protected:raise StateError('Invalid referral relationship')
   period=data.get('bonus_period',source['period']);days=BONUS.get(period)
   if days is None:raise StateError('Tariff has no referral bonus')
   con.execute('CREATE TABLE IF NOT EXISTS referral_awards(referred_client_id INTEGER PRIMARY KEY,referrer_client_id INTEGER NOT NULL,bonus_days INTEGER NOT NULL,awarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
   if con.execute('SELECT 1 FROM referral_awards WHERE referred_client_id=?',(source['id'],)).fetchone():raise StateError('Referral bonus already awarded')
   con.execute('INSERT INTO referral_awards(referred_client_id,referrer_client_id,bonus_days) VALUES(?,?,?)',(source['id'],row['id'],days))
   if row['period']=='lifetime':updates={'end_date':None}
   else:
    old=subscription_deadline(row['period'],row['end_date'])
    base=max(now,old) if old else now
    end=base+timedelta(days=days)
    updates={'end_date':end.date().isoformat() if len(row['end_date'] or '')==10 else end.isoformat()}
   return updates,'referral_bonus','Referral bonus +'+str(days)+' days from client #'+str(source['id']),None
  raise StateError('Unsupported subscription operation')

 @staticmethod
 def _notify(con,row,event):
  kind,end=event
  con.execute('CREATE TABLE IF NOT EXISTS subscription_notifications(event_key TEXT PRIMARY KEY,client_id INTEGER NOT NULL,event_type TEXT NOT NULL,end_date TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,delivered_at TEXT)')
  key=hashlib.sha256(json.dumps([row['id'],kind,end]).encode()).hexdigest()
  con.execute('INSERT OR IGNORE INTO subscription_notifications(event_key,client_id,event_type,end_date) VALUES(?,?,?,?)',(key,row['id'],kind,end))

 def tick(self,now=None):
  now=now or datetime.now(timezone.utc);processed=[]
  with self.connect() as con:
   deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
   rows=con.execute('SELECT * FROM clients'+(' WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients)' if deleted else '')).fetchall()
  for row in rows:
   cid=str(row['id'])
   if cid in self.protected:continue
   end=subscription_deadline(row['period'],row['end_date'])
   if end is None:continue
   if end<=now:
    key='expiry-'+hashlib.sha256(json.dumps([cid,row['end_date']]).encode()).hexdigest()
    self.change(cid,'expire',{},key,now);processed.append(cid)
   elif end-now<=timedelta(days=3):
    with self.locked():
     self._recover()
     with self.connect() as con:
      fresh=con.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone()
      deadline=subscription_deadline(fresh['period'],fresh['end_date']) if fresh else None
      if deadline is not None and now<deadline<=now+timedelta(days=3):
       self._notify(con,fresh,('three_days',fresh['end_date']))
  return processed
