"""Compare Caddy adapter structure without printing configuration values."""
import ast,importlib.util,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('release','/root/vpn-clashinstall.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
tree=ast.parse(Path('/root/vpn-clashinstall.py').read_text())
addition=next(ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='addition' for t in n.targets))
candidate=Path('/etc/caddy/clash-diff.caddy')
m.write(candidate,m.CADDY.read_bytes().replace(b'{',b'{'+addition,1))
try:
 a=json.loads(m.run('caddy','adapt','--config',str(m.CADDY),'--adapter','caddyfile'))
 b=json.loads(m.run('caddy','adapt','--config',str(candidate),'--adapter','caddyfile'));b.pop('logging',None)
 def diff(a,b,p=''):
  if type(a)!=type(b):return [p+' (type)']
  if isinstance(a,dict):return [p+'/'+k+' (key)' for k in a.keys()^b.keys()]+sum((diff(a[k],b[k],p+'/'+k) for k in a.keys()&b.keys()),[])
  if isinstance(a,list):return [p+' (length)'] if len(a)!=len(b) else sum((diff(x,y,p+'/'+str(i)) for i,(x,y) in enumerate(zip(a,b))),[])
  return [] if a==b else [p+' (value)']
 print(json.dumps(dict(changed_paths=diff(a,b))))
finally:candidate.unlink(missing_ok=True)
