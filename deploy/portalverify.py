from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
import json,sqlite3,time,uuid,subprocess
opener=build_opener(ProxyHandler({}))
def req(url,data=None,headers=None):
 h=dict(headers or {})
 if data is not None:h['Content-Type']='application/json'
 with opener.open(Request(url,data=None if data is None else json.dumps(data).encode(),headers=h),timeout=20) as r:return json.load(r)
env=dict(line.split('=',1) for line in Path('/etc/vpn-manager-live.env').read_text().splitlines() if '=' in line)
login=req('http://127.0.0.1:5056/auth/login',dict(code=env['PANEL_ADMIN_CODE']))
headers={'Authorization':'Bearer '+login['access_token']}
try:
 status=req('http://127.0.0.1:5056/api/connect/pilot',headers=headers);assert status['all_clients'] and status['code_required']
 with sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True) as db:
  cid,number=db.execute('SELECT c.id,l.label_number FROM clients c JOIN awg_profile_labels l ON l.client_id=c.id WHERE c.id NOT IN (SELECT client_id FROM awg_deleted_clients) AND c.wg_client_id IS NOT NULL ORDER BY c.id LIMIT 1').fetchone()
 result=req('http://127.0.0.1:5056/api/connect/clients/'+str(cid)+'/link',dict(action='issue'),dict(headers,**{'Idempotency-Key':'portal-admin-check-'+uuid.uuid4().hex}))
 assert result['label']=='CAESAR VPN #'+str(number) and len(result['access_code'])==12
 snapshot=json.loads(Path('/var/lib/vpn-manager-connect/material/portal.json').read_text());age=time.time()-snapshot['updated_at'];assert 0<=age<150
 for unit in ('vpn-manager-connect.service','vpn-manager-connect-snapshot.timer'):
  assert subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True).stdout.strip()=='active'
 print(json.dumps(dict(ok=True,admin_proxy_verified=True,legacy_client_number_matches=True,codes_available_to_admin=True,portals=len(status['client_ids']),snapshot_age_seconds=round(age),services_active=True)))
finally:req('http://127.0.0.1:5056/auth/logout',{},headers)
