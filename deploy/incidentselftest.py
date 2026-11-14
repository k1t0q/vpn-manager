"""Isolated AWG kernel handshake test. Never modifies the production interface."""
import os,sys,json,re,subprocess,tempfile,hashlib,signal,shutil
from pathlib import Path
os.umask(0o077)
def run(*args,input=None,check=True):
 r=subprocess.run(args,input=input,capture_output=True,text=True,timeout=18)
 if check and r.returncode:raise RuntimeError('Command failed: '+args[0]+'; exit='+str(r.returncode))
 return r
def invariants():
 c=Path('/etc/amnezia/amneziawg/awg-test.conf')
 return dict(config=hashlib.sha256(c.read_bytes()).hexdigest(),
 profiles={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')},
 peers=sorted(run('awg','show','awg-test','peers').stdout.split()),
 routes=run('ip','-j','route','show','table','all').stdout,
 rules=run('ip','-j','rule','show').stdout,
 xray=run('systemctl','show','xray','-p','MainPID','--value').stdout)
before=invariants()
temp=Path(tempfile.mkdtemp(prefix='vpn-kernel-selftest-',dir='/run'))
names=['k1t0check'+str(os.getpid())+'a','k1t0check'+str(os.getpid())+'b']
created=[];result={};stage='initial'
def interrupted(*args):raise RuntimeError('Interrupted')
signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
try:
 stage='read_public_obfuscation_parameters'
 header=Path('/etc/amnezia/amneziawg/awg-test.conf').read_text().split('[Peer]')[0]
 settings=[]
 for l in header.splitlines():
  if '=' in l:
   k,v=map(str.strip,l.split('=',1))
   if re.fullmatch(r'Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5]',k,re.I):settings.append(k+' = '+v)
 stage='create_isolated_namespaces'
 for n in names:
  run('ip','netns','add',n);created.append(n)
 run('ip','-n',names[0],'link','add','under-a','type','veth','peer','name','under-b')
 run('ip','-n',names[0],'link','set','under-b','netns',names[1])
 keys=[run('awg','genkey').stdout.strip() for _ in names]
 pubs=[run('awg','pubkey',input=k+'\n').stdout.strip() for k in keys]
 stage='configure_test_only_interfaces'
 for i,n in enumerate(names):
  def ns(*args):return run('ip','netns','exec',n,*args)
  under='under-a' if i==0 else 'under-b'
  run('ip','-n',n,'link','set','lo','up')
  run('ip','-n',n,'address','add','192.0.2.'+str(i+1)+'/24','dev',under)
  run('ip','-n',n,'link','set',under,'up')
  run('ip','-n',n,'link','add','awg-local','type','amneziawg')
  cfg='[Interface]\nPrivateKey = '+keys[i]+'\nListenPort = 59001\n'+'\n'.join(settings)+'\n[Peer]\nPublicKey = '+pubs[1-i]+'\nAllowedIPs = 10.254.254.'+str(2-i)+'/32\nEndpoint = 192.0.2.'+str(2-i)+':59001\n'
  p=temp/('test'+str(i)+'.conf');p.write_text(cfg);p.chmod(0o600)
  ns('awg','setconf','awg-local',str(p))
  run('ip','-n',n,'address','add','10.254.254.'+str(i+1)+'/32','dev','awg-local')
  run('ip','-n',n,'link','set','awg-local','up')
  run('ip','-n',n,'route','add','10.254.254.'+str(2-i)+'/32','dev','awg-local')
 stage='test_underlay'
 p=run('ip','netns','exec',names[0],'ping','-n','-c','1','-W','2','192.0.2.2',check=False)
 result['isolated_underlay_ok']=p.returncode==0
 stage='test_encrypted_tunnel'
 p=run('ip','netns','exec',names[0],'ping','-n','-c','3','-W','2','10.254.254.2',check=False)
 result['isolated_vpn_ping_ok']=p.returncode==0
 for i,n in enumerate(names):
  out=run('ip','netns','exec',n,'awg','show','awg-local','latest-handshakes').stdout
  result['test_peer_'+str(i)+'_handshake']=any(int(l.split()[-1])>0 for l in out.splitlines())
  out=run('ip','netns','exec',n,'awg','show','awg-local','transfer').stdout
  values=[list(map(int,l.split()[1:])) for l in out.splitlines()]
  result['test_peer_'+str(i)+'_transfer_bytes']=[sum(v[j] for v in values) for j in range(2)]
except Exception as e:
 result.update(error_class=type(e).__name__,failed_stage=stage)
finally:
 clean=True
 for n in reversed(created):
  if run('ip','netns','delete',n,check=False).returncode:clean=False
 shutil.rmtree(temp)
 result['isolated_resources_removed']=clean
 try:result['production_invariants_unchanged']=invariants()==before
 except Exception:result['production_invariants_unchanged']=False
 print(json.dumps(result))
