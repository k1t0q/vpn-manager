"""Temporary loopback handshake against production AWG. Preserves existing peers."""
import os,json,sys,re,fcntl,hashlib,sqlite3,subprocess,signal
from pathlib import Path
from datetime import datetime,timezone
os.umask(0o077)
def run(*args,input=None,check=True):
 r=subprocess.run(args,input=input,capture_output=True,text=True,timeout=15)
 if check and r.returncode:raise RuntimeError('Command failed; exit='+str(r.returncode))
 return r
def snapshot():
 return dict(config=hashlib.sha256(config.read_bytes()).hexdigest(),
 profiles={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in profiles.glob('*/client.conf')},
 peers=sorted(run('awg','show','awg-test','peers').stdout.split()),
 routes=run('ip','-j','route','show','table','all').stdout,
 rules=run('ip','-j','rule','show').stdout,
 xray=run('systemctl','show','xray','-p','MainPID','--value').stdout)
config=Path('/etc/amnezia/amneziawg/awg-test.conf');profiles=Path('/opt/vpn-manager/awg-clients')
state=Path('/var/lib/vpn-manager-production/operations')
if (state/'pending.json').exists():raise SystemExit('Pending operation; no test started')
lock=open(state/'lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
before=snapshot()
address='10.10.0.254'
for p in profiles.glob('*/metadata.json'):
 if json.loads(p.read_text()).get('address')==address:raise SystemExit('Test address is occupied')
import ipaddress
for line in run('awg','show','awg-test','allowed-ips').stdout.splitlines():
 for value in line.split()[1:]:
  for net in value.split(','):
   if net!='(none)' and ipaddress.ip_address(address) in ipaddress.ip_network(net,strict=False):raise SystemExit('Test address overlaps live peer')
ns='k1t0livecheck'+str(os.getpid());dev='awgd'+str(os.getpid());unit='vpn-loopcheck-'+str(os.getpid())
if run('ip','link','show',dev,check=False).returncode==0 or ns in run('ip','netns','list').stdout:raise SystemExit('Test resource already exists')
backup=Path('/root/vpn-manager-backups')/('live-loop-test-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700)
(backup/'awg-test.conf').write_bytes(config.read_bytes())
(backup/'invariants.json').write_text(json.dumps(before))
db=Path('/opt/vpn-manager/data/clients-staging.db')
with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as con:
 with sqlite3.connect(backup/'clients.db') as out:
  con.backup(out)
  if out.execute('pragma integrity_check').fetchone()[0]!='ok':raise SystemExit('Backup integrity failure')
priv=run('awg','genkey').stdout.strip();pub=run('awg','pubkey',input=priv+'\n').stdout.strip()
psk=run('awg','genpsk').stdout.strip()
if pub in before['peers']:raise SystemExit('Unexpected key collision')
cleanup=backup/'rollback.py'
cleanup.write_text('import subprocess\n'+
 'def r(*a): return subprocess.run(a,capture_output=True,timeout=15)\n'+
 'r("awg","set","awg-test","peer",'+repr(pub)+',"remove")\n'+
 'r("ip","netns","delete",'+repr(ns)+')\n'+
 'r("ip","link","delete",'+repr(dev)+')\n')
cleanup.chmod(0o700)
run('systemd-run','--quiet','--unit',unit,'--on-active=90s','/usr/bin/python3',str(cleanup))
result={'backup':str(backup)};stage='configure_temporary_peer'
def interrupted(*args):raise RuntimeError('Interrupted')
signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
try:
 peer=backup/'temporary-peer.conf'
 peer.write_text('[Peer]\nPublicKey = '+pub+'\nPresharedKey = '+psk+'\nAllowedIPs = '+address+'/32\n')
 settings=[]
 for line in config.read_text().split('[Peer]')[0].splitlines():
  if '=' in line:
   k,v=map(str.strip,line.split('=',1))
   if re.fullmatch(r'Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5]',k,re.I):settings.append(k+' = '+v)
 serverpub=run('awg','show','awg-test','public-key').stdout.strip()
 client=backup/'temporary-client.conf'
 client.write_text('[Interface]\nPrivateKey = '+priv+'\n'+'\n'.join(settings)+'\n[Peer]\nPublicKey = '+serverpub+'\nPresharedKey = '+psk+'\nEndpoint = 127.0.0.1:51840\nAllowedIPs = 10.10.0.1/32\n')
 run('ip','netns','add',ns)
 run('ip','link','add',dev,'type','amneziawg')
 run('awg','setconf',dev,str(client))
 run('ip','link','set',dev,'netns',ns)
 run('ip','-n',ns,'link','set','lo','up')
 run('ip','-n',ns,'address','add',address+'/32','dev',dev)
 run('ip','-n',ns,'link','set',dev,'up')
 run('ip','-n',ns,'route','add','10.10.0.1/32','dev',dev)
 run('awg','addconf','awg-test',str(peer))
 stage='ping_production_tunnel'
 r=run('ip','netns','exec',ns,'ping','-n','-c','3','-W','2','10.10.0.1',check=False)
 result['production_tunnel_ping_ok']=r.returncode==0
 out=run('ip','netns','exec',ns,'awg','show',dev,'latest-handshakes').stdout
 result['production_handshake_ok']=any(int(l.split()[-1])>0 for l in out.splitlines())
 out=run('ip','netns','exec',ns,'awg','show',dev,'transfer').stdout
 result['test_transfer_bytes']=[list(map(int,l.split()[1:])) for l in out.splitlines()]
except Exception as e:result.update(error_class=type(e).__name__,failed_stage=stage)
finally:
 run('/usr/bin/python3',str(cleanup),check=False)
 after=snapshot()
 result['production_invariants_unchanged']=after==before
 result['temporary_peer_removed']=pub not in after['peers']
 result['temporary_namespace_removed']=ns not in run('ip','netns','list').stdout
 if result['temporary_peer_removed'] and result['temporary_namespace_removed']:
  run('systemctl','stop',unit+'.timer',check=False)
 result['vpn_restarted']=False
 print(json.dumps(result))
