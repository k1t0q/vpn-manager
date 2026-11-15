"""Incident evidence collection only. Never changes routing, firewall or VPN."""
from pathlib import Path
from datetime import datetime,timezone
import ast,hashlib,json,os,re,shlex,shutil,sqlite3,subprocess,tarfile,time
def run(args):
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=20)
  return dict(code=r.returncode,out=r.stdout,err=r.stderr)
 except Exception as e:return dict(code=-1,out='',err=type(e).__name__)
def main():
 assert os.geteuid()==0;os.umask(0o077)
 root=Path('/root/vpn-incident-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));root.mkdir(mode=0o700)
 commands={
 'routes':['ip','-j','route','show','table','all'],'rules':['ip','-j','rule','show'],'links':['ip','-j','address','show'],
 'iptables':['iptables-save','-c'],'ip6tables':['ip6tables-save','-c'],'nft':['nft','-j','list','ruleset'],
 'listeners':['ss','-H','-lntup'],'sysctl':['sysctl','net.ipv4.ip_forward','net.ipv4.conf.all.rp_filter','net.ipv4.conf.eth0.rp_filter','net.ipv4.tcp_syncookies','net.ipv4.tcp_timestamps'],
 'services':['systemctl','list-units','--all','--no-pager','--plain','--type=service'],
 'route-client':['ip','route','get','81.162.212.6'],'route-external':['ip','route','get','1.1.1.1'],
 'awg-peers':['awg','show','awg-test','latest-handshakes'],'wg-peers':['wg','show','wg-test','latest-handshakes']}
 results={}
 for name,args in commands.items():
  r=run(args);results[name]=r;(root/(name+'.json')).write_text(json.dumps(r))
 paths=['/etc/netplan','/etc/network/interfaces','/etc/network/interfaces.d','/etc/sysctl.conf','/etc/sysctl.d','/etc/iptables','/etc/nftables.conf','/etc/amnezia/amneziawg','/etc/wireguard','/etc/caddy/Caddyfile','/etc/systemd/system','/root/vpn-clashinstall.py','/root/vpn-clashuninstall.py','/root/vpn-clashrollback.py']
 with tarfile.open(root/'config-evidence.tar.gz','w:gz') as archive:
  for p in paths:
   if Path(p).exists():archive.add(p,arcname=p.lstrip('/'))
 db=Path('/opt/vpn-manager/data/clients-staging.db')
 if db.exists():
  with sqlite3.connect(root/'clients.db') as dest,sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src:src.backup(dest)
 hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')}
 (root/'profile-hashes.json').write_text(json.dumps(hashes));Path('/root/vpn-incident-latest').write_text(str(root))
 def parsed(name):
  try:return json.loads(results[name]['out'])
  except Exception:return []
 firewall=[]
 for line in results['iptables']['out'].splitlines():
  if line.startswith(('*',':','COMMIT')):firewall.append(line)
  elif ' -A ' in line or line.startswith('-A '):
   parts=shlex.split(line);filtered=[];skip=False
   for i,x in enumerate(parts):
    if skip:skip=False;continue
    if x=='--comment':skip=True;continue
    filtered.append(x)
   firewall.append(' '.join(filtered))
 (root/'firewall-readable.json').write_text(json.dumps(firewall))
 scripts={}
 for p in (Path('/root/vpn-clashinstall.py'),Path('/root/vpn-clashuninstall.py')):
  if p.exists():
   text=p.read_text(errors='replace')
   scripts[p.name]=dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest(),network_mentions={word:[i+1 for i,line in enumerate(text.splitlines()) if word in line] for word in ['iptables','nft','ip route','ip rule','wg-quick','awg-quick','sysctl','tun','mihomo']})
 (root/'scripts-summary.json').write_text(json.dumps(scripts))
 summary=dict(backup=str(root),routes=parsed('routes'),rules=parsed('rules'),sysctl=results['sysctl']['out'].splitlines(),route_client=results['route-client']['out'].strip(),route_external=results['route-external']['out'].strip(),interfaces=[dict(name=x['ifname'],state=x.get('operstate'),addresses=[a['local'] for a in x.get('addr_info',[])]) for x in parsed('links')],vpn={})
 for name in ['awg-peers','wg-peers']:
  times=[int(line.split()[-1]) for line in results[name]['out'].splitlines() if line.split()[-1].isdigit()]
  summary['vpn'][name]=dict(peers=len(times),recent_180s=sum(t>time.time()-180 for t in times),newest_age_seconds=int(time.time()-max(times)) if times and max(times)>0 else None)
 print(json.dumps(summary,indent=1))
if __name__=='__main__':main()
