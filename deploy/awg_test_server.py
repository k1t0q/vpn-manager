"""Frozen root service for a private staging DB and newly created test peers only."""
from http.server import HTTPServer,BaseHTTPRequestHandler
import hmac,json,os,re
from pathlib import Path
import sqlite3
from awg_persistence import StateError,AWGRuntime
from awg_provision import Provisioner
from awg_test_bridge import TestBridge
from awg_readonly_bridge import collect

ROOT=Path('/var/lib/vpn-manager-awg-stage')


class Handler(BaseHTTPRequestHandler):
 def log_message(self,*_):pass
 def reply(self,status,body,content_type='application/json'):
  data=body if isinstance(body,bytes) else json.dumps(body,ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type',content_type)
  self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
 def handle_request(self):
  token=self.headers.get('X-VPN-Token','')
  if not token or not hmac.compare_digest(token.encode(),self.server.token.encode()):return self.reply(401,dict(ok=False,error='Unauthorized'))
  try:
   if (self.server.bridge.store.state/'pending.json').exists():
    self.server.bridge.store.recover()
   data=None
   if self.command=='POST':
    length=int(self.headers.get('Content-Length','0'))
    if not 0<=length<=16384:return self.reply(413,dict(ok=False,error='Request too large'))
    data=json.loads(self.rfile.read(length) or b'{}')
   history=re.fullmatch(r'/api/managed/clients/([0-9]{1,10})/history',self.path)
   if self.command=='GET' and history:
    with self.server.bridge.store.connect() as con:
     rows=con.execute('SELECT event_type,description,created_at FROM client_history WHERE client_id=? ORDER BY created_at DESC,id DESC',(history[1],)).fetchall()
    return self.reply(200,dict(ok=True,history=[dict(row) for row in rows]))
   if self.command=='GET' and self.path=='/api/managed/clients':
    with self.server.bridge.store.connect() as con:
     deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
     rows=con.execute('SELECT id,name,phone,start_date,end_date,period,note,created_at,referrer_id,wg_client_id FROM clients'+(' WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients)' if deleted else '')+' ORDER BY id').fetchall()
    return self.reply(200,dict(ok=True,clients=[dict(row) for row in rows]))
   if self.command=='GET' and self.path=='/api/wg/clients':
    peers=collect(self.server.bridge.store.db_path,self.server.bridge.store.profiles)
    with self.server.bridge.store.connect() as con:
     deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
     removed={row[0] for row in con.execute('SELECT client_id FROM awg_deleted_clients')} if deleted else set()
    peers=[peer for peer in peers if peer['client_id'] not in removed]
    return self.reply(200,dict(ok=True,clients=peers,test_only=True))
   body,status,kind=self.server.bridge.dispatch(self.command,self.path,data,self.headers.get('Idempotency-Key',''))
   return self.reply(status,body,kind)
  except StateError as error:
   try:
    if (self.server.bridge.store.state/'pending.json').exists():self.server.bridge.store.recover()
   except Exception:return self.reply(503,dict(ok=False,error='Test operation pending safe recovery'))
   return self.reply(409,dict(ok=False,error=str(error)))
  except Exception:
   # Recovery is safe only for a durable journal belonging to this frozen engine.
   try:self.server.bridge.store.recover()
   except Exception:pass
   return self.reply(503,dict(ok=False,error='Test operation unavailable; retry with the same request key'))
 def do_GET(self):self.handle_request()
 def do_POST(self):self.handle_request()


def main():
 baseline=json.loads((ROOT/'baseline.json').read_text())
 store=Provisioner(ROOT/'clients.db',Path('/etc/amnezia/amneziawg/awg-test.conf'),
                   ROOT/'profiles',ROOT/'operations',protected_ids=baseline['protected_ids'])
 store.stage_limit=3
 # Owner choice is server-side and restricted to immutable protected records.
 # Legacy imports may contain several owners; never rewrite those rows or
 # accept a caller-provided user_id to resolve that difference.
 with store.connect() as con:
  owners=con.execute('SELECT id,user_id FROM clients WHERE user_id IS NOT NULL ORDER BY id').fetchall()
 owner=next((row['user_id'] for row in owners if str(row['id']) in store.protected and str(row['user_id']).strip()),None)
 if owner is None:raise SystemExit('No protected staging owner available')
 store.owner_id=owner
 store.recover(offline=AWGRuntime().is_down())
 bridge=TestBridge(store,ROOT/'template.conf',baseline)
 server=HTTPServer(('127.0.0.1',5054),Handler)
 token=os.environ.get('PANEL_AWG_TOKEN','')
 if len(token)<32:raise SystemExit('Bridge token not configured')
 server.token=token;server.bridge=bridge;server.serve_forever()


if __name__=='__main__':main()
