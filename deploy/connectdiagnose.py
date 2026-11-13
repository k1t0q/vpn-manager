from pathlib import Path
import json,subprocess
s=json.loads(Path('/root/vpn-connect-pilot-install.json').read_text())
def adapt(p):return json.loads(subprocess.run(['caddy','adapt','--config',str(p),'--adapter','caddyfile'],capture_output=True,text=True,check=True).stdout)
def info(d):
 return {k:{'listen':v.get('listen'),'routes':len(v.get('routes',[]))} for k,v in d['apps']['http']['servers'].items()}
a=adapt(Path(s['backup'])/'Caddyfile');b=adapt(Path('/etc/caddy/.connect-candidate'))
print('BEFORE '+json.dumps(info(a)));print('AFTER '+json.dumps(info(b)))
print('IMPORTS '+json.dumps([l for l in (Path(s['backup'])/'Caddyfile').read_text().splitlines() if l.strip().startswith('import ')]))
def shape(d):
 out=[]
 def walk(v,path=''):
  if isinstance(v,dict):
   for k,item in v.items():
    if k in ['host','path','dial','root','handler','group','terminal']:out.append([path+'/'+k,item])
    elif isinstance(item,(list,dict)):walk(item,path+'/'+k)
  elif isinstance(v,list):
   for i,item in enumerate(v):walk(item,path+'/'+str(i))
 walk(d);return out



from collections import Counter
def counts(d):return Counter(x for v in d['apps']['http']['servers'].values() for x in v.get('listen',[]))
print('COUNTERS '+str(counts(a))+' -> '+str(counts(b)))
def normalize(route):
 names={}
 def walk(v):
  if isinstance(v,dict):return {k:(names.setdefault(x,'g'+str(len(names))) if k=='group' and isinstance(x,str) else walk(x)) for k,x in v.items()}
  if isinstance(v,list):return [walk(x) for x in v]
  return v
 return json.dumps(walk(route),sort_keys=True)
old=[r for v in a['apps']['http']['servers'].values() for r in v.get('routes',[])]
new=[r for v in b['apps']['http']['servers'].values() for r in v.get('routes',[])]
for i,r in enumerate(old):print('ROUTE '+str(i)+' preserved='+str(normalize(r) in [normalize(x) for x in new]))
