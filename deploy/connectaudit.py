"""Read-only inventory for connect.caesarbot.ru; no secrets in output."""
from pathlib import Path
import hashlib,json,os,re,socket,sqlite3,subprocess,sys
from urllib.request import urlopen,Request
os.umask(0o077)
ROOT=Path('/var/lib/vpn-manager-production')
DB=Path('/opt/vpn-manager/data/clients-staging.db')
PROFILES=Path('/opt/vpn-manager/awg-clients')
CONFIG=Path('/etc/amnezia/amneziawg/awg-test.conf')
def run(*a):return subprocess.run(a,capture_output=True,text=True,timeout=20).stdout.strip()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
report={'hostname':socket.gethostname(),'addresses':run('hostname','-I').split()}
report['services']={name:run('systemctl','show',name,'-p','ActiveState','-p','SubState','-p','UnitFileState','-p','MainPID').splitlines() for name in ['caddy.service','xray.service','vpn-manager.service','vpn-manager-live-api.service','vpn-manager-live-bridge.service','vpn-manager-push.timer','awg-quick@awg-test.service']}
report['listeners']=run('ss','-lntu').splitlines()
report['dns']={}
for host in ['connect.caesarbot.ru','caesarbot.ru']:
 try:report['dns'][host]=sorted({x[4][0] for x in socket.getaddrinfo(host,None)})
 except OSError as e:report['dns'][host]=type(e).__name__
try:
 with urlopen('https://dns.google/resolve?name=connect.caesarbot.ru&type=A',timeout=10) as r:
  answer=json.load(r);report['public_dns']={'status':answer.get('Status'),'answers':answer.get('Answer',[])}
except Exception as e:report['public_dns']={'error':type(e).__name__}
report['https']={}
for url in ['https://connect.caesarbot.ru/','https://185-23-239-142.sslip.io:8444/panel-live/']:
 try:
  with urlopen(Request(url,method='HEAD'),timeout=10) as r:report['https'][url]={'status':r.status}
 except Exception as e:report['https'][url]={'error':type(e).__name__}
cfg=json.loads(run('caddy','adapt','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'))
servers=cfg.get('apps',{}).get('http',{}).get('servers',{})
report['caddy']={name:{'listen':data.get('listen'), 'access_logging':bool(data.get('logs'))} for name,data in servers.items()}
def routes(value):
 result=[]
 if isinstance(value,dict):
  for key,item in value.items():
   if key in ['host','path','dial','root','handler'] and isinstance(item,(str,list)):result.append({key:item})
   elif isinstance(item,(dict,list)):result+=routes(item)
 elif isinstance(value,list):
  for item in value:result+=routes(item)
 return result
report['caddy_routes']=routes(servers)
sys.path.insert(0,'/usr/local/libexec/vpn-manager-production')
from awg_persistence import peer_blocks
_,blocks=peer_blocks(CONFIG.read_text());disk={key:fields for key,fields,_ in blocks}
live=set(run('awg','show','awg-test','peers').split())
with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as con:
 con.row_factory=sqlite3.Row
 archived=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
 deleted={str(r[0]) for r in con.execute('SELECT client_id FROM awg_deleted_clients')} if archived else set()
 rows=[dict(r) for r in con.execute('SELECT id,wg_client_id FROM clients')]
 integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
active=[r for r in rows if str(r['id']) not in deleted]
mismatches=[];known=set();enabled=0
for row in active:
 cid=str(row['id']);p=PROFILES/cid
 try:
  meta=json.loads((p/'metadata.json').read_text());key=meta['public_key'];known.add(key)
  assert str(meta['client_id'])==cid and (p/'client.conf').is_file()
  on=meta.get('enabled') is not False
  assert (key in disk)==on and (key in live)==on
  enabled+=int(on)
 except Exception:mismatches.append(cid)
baseline=json.loads((ROOT/'baseline.json').read_text());changed=[]
for cid,original in baseline['clients'].items():
 p=PROFILES/cid/'metadata.json'
 if cid in deleted:continue
 try:
  m=json.loads(p.read_text())
  if m['public_key']!=original['public_key'] or m['address']!=original['address']:changed.append(cid)
 except Exception:changed.append(cid)
report['clients']={'rows_total':len(rows),'visible':len(active),'archived':len(deleted),'enabled':enabled,'runtime_peers':len(live),'disk_peers':len(disk),'unmanaged_runtime_peers':len(live-known),'mismatch_ids':mismatches,'original_identity_changes':changed,'sqlite_integrity':integrity}
report['config_structure']={'sections':re.findall(r'^\\[([^]]+)\\]',CONFIG.read_text(),re.M),'interface_fields':sorted({line.split('=',1)[0].strip() for line in CONFIG.read_text().split('[Peer]',1)[0].splitlines() if '=' in line and not line.lstrip().startswith('#')})}
report['files']={}
for p in [Path('/opt/vpn-manager/app.py'),Path('/opt/vpn-manager/awg_adapter.py'),Path('/opt/vpn-manager-production/server/production_app.py'),Path('/opt/vpn-manager-production/server/staging_api.py'),Path('/opt/vpn-manager-production/public/index.html'),Path('/opt/vpn-manager-production/public/sqlite-manager.js'),Path('/etc/caddy/Caddyfile')]:
 if p.exists():report['files'][str(p)]=digest(p)
api=Path('/opt/vpn-manager-production/server/staging_api.py').read_text()
report['api_routes']=sorted(set(re.findall(r"@app\\.(?:get|post|route)\\('([^']+)'",api)))
report['boot_exec']=run('systemctl','show','awg-quick@awg-test.service','-p','ExecStart')
report['vpn_configuration_hash']=digest(CONFIG)
private=dict(report,profile_hashes={str(p.parent.name):digest(p) for p in PROFILES.glob('*/client.conf')})
Path('/root/vpn-connect-audit.json').write_text(json.dumps(private,indent=2))
print(json.dumps(report,indent=2))
