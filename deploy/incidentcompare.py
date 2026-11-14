"""Compare deployed Caddy routes with the private pre-deploy backup. No mutation."""
from pathlib import Path
import hashlib,json,subprocess
def adapt(path):
 r=subprocess.run(['caddy','adapt','--config',str(path),'--adapter','caddyfile'],capture_output=True,text=True,timeout=20,check=True)
 return json.loads(r.stdout)
oldpath=Path('/root/vpn-manager-backups/clash-mi-20261114T000324Z/4');newpath=Path('/etc/caddy/Caddyfile')
old=adapt(oldpath);new=adapt(newpath)
def normalize(obj):
 obj=json.loads(json.dumps(obj));obj.pop('logging',None)
 def walk(v):
  if isinstance(v,dict):
   if v.get('handler')=='file_server' and isinstance(v.get('hide'),list):v['hide']=sorted('<CADDYFILE>' if x in [str(oldpath),str(newpath)] else x for x in v['hide'])
   for x in v.values():walk(x)
  elif isinstance(v,list):
   for x in v:walk(x)
 walk(obj);return obj
def routes(v,found):
 if isinstance(v,dict):
  if v.get('handler')=='reverse_proxy':found.extend(x.get('dial') for x in v.get('upstreams',[]))
  for x in v.values():routes(x,found)
 elif isinstance(v,list):
  for x in v:routes(x,found)
targets=[];routes(new,targets)
files=[dict(path=str(p),mtime=int(p.stat().st_mtime)) for p in [Path('/etc/amnezia/amneziawg/awg-test.conf'),Path('/etc/wireguard/wg-test.conf')] if p.exists()]
scripts=[str(p) for base in ['/root','/opt','/tmp'] for p in Path(base).rglob('*clash*uninstall*.py')]
print(json.dumps(dict(caddy_routes_unchanged_since_predeploy=normalize(old)==normalize(new),upstreams=targets,vpn_config_mtimes=files,uninstall_scripts_found=scripts)))
