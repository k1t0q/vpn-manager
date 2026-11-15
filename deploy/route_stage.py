"""Add isolated staging routes inside the existing 8443 site only.
Never changes listeners, production routes, AWG, Xray, certificates or auth.
"""
import datetime,json,os
from pathlib import Path
import shutil,subprocess,tempfile

CONFIG=Path('/etc/caddy/Caddyfile')
SNIPPET=Path('/etc/caddy/vpn-manager-stage.caddy')
IMPORT='    import /etc/caddy/vpn-manager-stage.caddy'
ROUTES='''handle_path /panel-stage/* {
    root * /srv/vpn-manager-stage/current/public
    file_server
}
handle_path /stage-api/* {
    reverse_proxy 127.0.0.1:5053
}
'''

def run(*args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=30)
 if r.returncode:raise RuntimeError('Caddy operation failed; original config preserved')
 return r.stdout

def adapt(path):
 return json.loads(run('caddy','adapt','--config',str(path),'--adapter','caddyfile'))

def listeners(config):
 servers=config.get('apps',{}).get('http',{}).get('servers',{})
 return sorted((name,tuple(sorted(server.get('listen',[])))) for name,server in servers.items())

def clean_line(line):
 out=[];quote=None;escaped=False
 for c in line:
  if escaped:escaped=False;continue
  if c=='\\' and quote:escaped=True;continue
  if quote:
   if c==quote:quote=None
   continue
  if c in ('"','`'):quote=c;continue
  if c=='#':break
  out.append(c)
 return ''.join(out)

def candidate(text):
 lines=text.splitlines(True)
 headers=[i for i,line in enumerate(lines) if ':8443' in clean_line(line) and clean_line(line).rstrip().endswith('{')]
 if len(headers)!=1:raise RuntimeError('Expected exactly one 8443 site; no changes')
 start=headers[0];depth=0;end=None
 for i in range(start,len(lines)):
  line=clean_line(lines[i]);depth+=line.count('{')-line.count('}')
  if depth==0:end=i;break
 if end is None:raise RuntimeError('Cannot identify 8443 site; no changes')
 if any(IMPORT.strip()==line.strip() for line in lines[start:end]):
  return text
 lines.insert(start+1,IMPORT+'\n')
 return ''.join(lines)

def replace(path,data,stat):
 fd,name=tempfile.mkstemp(prefix='.stage-',dir=path.parent)
 try:
  os.fchmod(fd,stat.st_mode & 0o777);os.fchown(fd,stat.st_uid,stat.st_gid)
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)

def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 if CONFIG.is_symlink():raise SystemExit('Review symlinked Caddy configuration first')
 original=CONFIG.read_bytes();stat=CONFIG.stat();text=original.decode()
 changed=candidate(text)
 before=adapt(CONFIG)
 if not any(':8443' in listen for _,group in listeners(before) for listen in group):
  raise SystemExit('8443 listener not present')
 if SNIPPET.exists() and SNIPPET.read_text()!=ROUTES:
  raise SystemExit('Existing staging snippet differs; not overwritten')
 if changed==text and SNIPPET.exists():
  print('STAGING_ROUTES_ALREADY_PRESENT');return
 backup=Path('/root/vpn-manager-backups')/('stage-route-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
 backup.mkdir(parents=True,mode=0o700)
 shutil.copy2(CONFIG,backup/'Caddyfile')
 if SNIPPET.exists():shutil.copy2(SNIPPET,backup/'staging.caddy')
 else:SNIPPET.write_text(ROUTES);SNIPPET.chmod(0o644)
 fd,tmp=tempfile.mkstemp(prefix='.stage-candidate-',dir=CONFIG.parent);os.close(fd)
 trial=Path(tmp)
 try:
  trial.write_text(changed)
  after=adapt(trial)
  if listeners(before)!=listeners(after):raise RuntimeError('Listener change rejected')
  run('caddy','validate','--config',str(trial),'--adapter','caddyfile')
  replace(CONFIG,changed.encode(),stat)
  try:run('caddy','reload','--config',str(CONFIG),'--adapter','caddyfile')
  except Exception:
   replace(CONFIG,original,stat)
   run('caddy','reload','--config',str(CONFIG),'--adapter','caddyfile')
   raise
 finally:
  trial.unlink(missing_ok=True)
 print(json.dumps({'ok':True,'listeners_unchanged':True,'paths':['/panel-stage/','/stage-api/'],'backup':str(backup)}))

if __name__=='__main__':main()
