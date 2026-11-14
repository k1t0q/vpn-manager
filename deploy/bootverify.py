"""Private authenticated smoke test; never prints credentials or client data."""
import json,os,subprocess
from pathlib import Path
from urllib.request import Request,urlopen
os.environ['NO_PROXY']='127.0.0.1,localhost'
def call(path,data=None,token=None):
    h={'Content-Type':'application/json'}
    if token:h['Authorization']='Bearer '+token
    req=Request('http://127.0.0.1:5056'+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
    with urlopen(req,timeout=35) as r:return r.status,json.load(r)
env={}
for line in Path('/etc/vpn-manager-live.env').read_text().splitlines():
    if '=' in line:
        k,v=line.split('=',1);env[k]=v
result={}
token=None
try:
    status,d=call('/auth/login',{'code':env['PANEL_ADMIN_CODE']});token=d.get('access_token')
    result['login_http']=status
    status,d=call('/api/clients',token=token)
    result.update(clients_http=status,clients_count=len(d.get('clients',[])),clients_ok=d.get('ok'))
    status,d=call('/health')
    result['api_health']={k:d.get(k) for k in ('ok','scheduler_ok','auto_expiry','read_only')}
except Exception as e:result['error_class']=type(e).__name__
finally:
    if token:
        try:call('/auth/logout',{},token)
        except Exception:pass
result['services']={}
for u in ['awg-quick@awg-test','vpn-manager-live-bridge','vpn-manager-live-api','caddy','xray']:
    result['services'][u]=subprocess.run(['systemctl','is-active',u],capture_output=True,text=True).stdout.strip()
r=subprocess.run(['awg','show','awg-test','latest-handshakes'],capture_output=True,text=True)
import time
ages=[int(time.time())-int(l.split()[1]) for l in r.stdout.splitlines() if len(l.split())==2 and int(l.split()[1])>0]
result['vpn']={'peers':len(r.stdout.splitlines()),'handshakes_within_180s':sum(0<=a<=180 for a in ages)}
print(json.dumps(result))
