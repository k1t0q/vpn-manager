"""Non-mutating focused incident checks. No secrets or full configuration output."""
from pathlib import Path
import hashlib,json,re,subprocess,time,urllib.request,urllib.error
root=Path(Path('/root/vpn-incident-latest').read_text().strip())
def run(args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=20)
 return r.returncode,r.stdout
def get(url):
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=5) as r:return dict(status=r.status)
 except urllib.error.HTTPError as e:return dict(status=e.code)
 except Exception as e:return dict(error=type(e).__name__)
print('BACKUP '+str(root),flush=True)
for name in ['rules','route-client','route-external','sysctl']:
 print(name+': '+json.loads((root/(name+'.json')).read_text())['out'].strip(),flush=True)
nft=json.loads((root/'nft.json').read_text());data=json.loads(nft['out']) if nft['code']==0 else {}
print('NFT_TABLES '+json.dumps([x['table'] for x in data.get('nftables',[]) if 'table' in x]),flush=True)
code,text=run(['caddy','adapt','--config','/etc/caddy/Caddyfile','--adapter','caddyfile']);config=json.loads(text)
def tree(item):
 if isinstance(item,dict):
  result={}
  for k in ('listen','match','handler'):
   if k in item:result[k]=item[k]
  if 'upstreams' in item:result['upstreams']=[x.get('dial') for x in item['upstreams']]
  children=[tree(v) for k,v in item.items() if k in ('routes','handle','servers','http','apps')]
  if result:return dict(result,children=[x for x in children if x])
  return [x for x in children if x]
 if isinstance(item,list):return [tree(x) for x in item]
 return None
print('CADDY_ROUTES '+json.dumps(tree(config)),flush=True)
print('HTTP '+json.dumps({url:get(url) for url in ['http://127.0.0.1:5056/health','http://127.0.0.1:5056/api/health','https://185-23-239-142.sslip.io:8444/api-live/health','https://185-23-239-142.sslip.io:8444/api-live/api/health','https://185-23-239-142.sslip.io:8443/health']}),flush=True)
def counters():
 code,out=run(['awg','show','awg-test','transfer']);values=[]
 for line in out.splitlines():
  parts=line.split()
  if len(parts)==3:values.append((int(parts[1]),int(parts[2])))
 return (sum(x[0] for x in values),sum(x[1] for x in values))
first=counters();time.sleep(8);last=counters()
print('VPN_BYTES_8_SECONDS '+json.dumps(dict(received=last[0]-first[0],sent=last[1]-first[1])),flush=True)
changes=[]
for old in Path('/root/vpn-manager-backups/clash-mi-20261114T000324Z').glob('clients.db'):
 pass
files=[Path('/etc/amnezia/amneziawg/awg-test.conf'),*Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')]
baseline=json.loads((root/'profile-hashes.json').read_text())
print('PROFILES_UNCHANGED_SINCE_INCIDENT_BACKUP '+str(all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in baseline.items())),flush=True)
