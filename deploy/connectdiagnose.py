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
print('OLD '+json.dumps(shape(a['apps']['http']['servers'])))
print('NEW '+json.dumps(shape(b['apps']['http']['servers'])))
