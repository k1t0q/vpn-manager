from pathlib import Path
import json,subprocess
r=subprocess.run(['caddy','adapt','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'],capture_output=True,text=True,check=True)
d=json.loads(r.stdout)
h=d.get('apps',{}).get('http',{})
print(json.dumps({'http_port':h.get('http_port'),'https_port':h.get('https_port'),'servers':{k:{'listen':v.get('listen'),'automatic_https':v.get('automatic_https')} for k,v in h.get('servers',{}).items()}}))
for p in d.get('apps',{}).get('tls',{}).get('automation',{}).get('policies',[]):
 print(json.dumps({'subjects':p.get('subjects'),'issuers':[{'module':i.get('module'),'ca':i.get('ca'),'challenges':i.get('challenges')} for i in p.get('issuers',[])]}))
print(subprocess.run(['ufw','status'],capture_output=True,text=True).stdout)
