"""Recover the existing AWG interface and boot persistence, with timed scoped rollback."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,shutil,sqlite3,subprocess,tarfile,tempfile,urllib.request
HELPER_RELEASE='e27b0c4482a85eae47536ac0f3d257fe530ae956'
HELPER_BLOB='d2b96561c429ad6246592acf0bffe58dd99a1247'
UNIT='awg-quick@awg-test.service'
CONFIG=Path('/etc/amnezia/amneziawg/awg-test.conf')
HELPER=Path('/usr/local/libexec/vpn-manager-awg-network.py')
DROP=Path('/etc/systemd/system/awg-quick@awg-test.service.d/50-vpn-manager.conf')
RULES=[
 ['-t','filter','FORWARD','-i','eth0','-o','awg-test','-m','conntrack','--ctstate','RELATED,ESTABLISHED','-j','ACCEPT'],
 ['-t','filter','FORWARD','-i','awg-test','-o','eth0','-j','ACCEPT'],
 ['-t','nat','POSTROUTING','-s','10.10.0.0/24','-o','eth0','-j','MASQUERADE']]
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def run(args,check=True):
 r=subprocess.run(args,capture_output=True,text=True,timeout=45)
 if check and r.returncode:raise RuntimeError('Command failed')
 return r
def write(p,b,mode=0o644):
 fd,n=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),mode);f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(n,p)
def iptables(rule,op):
 flag,table,chain,*match=rule
 return ['/usr/sbin/iptables','-w','5',flag,table,op,chain,*match]
def parts(text):
 records=[];row=None
 for line in text.splitlines():
  line=line.strip()
  if not line or line.startswith('#'):continue
  if line.startswith('['):row={};records.append(row)
  elif row is not None and '=' in line:
   k,v=line.split('=',1);row[k.strip()]=v.strip()
 return records
def main():
 assert os.geteuid()==0;os.umask(0o077)
 assert not Path('/sys/class/net/awg-test').exists()
 assert not HELPER.exists() and not DROP.exists()
 assert '185.23.239.142' in run(['ip','-4','addr','show','dev','eth0']).stdout
 assert Path('/proc/sys/net/ipv4/ip_forward').read_text().strip()=='1'
 config=CONFIG.read_bytes();rows=parts(config.decode());server=rows[0]
 assert server.get('Address')=='10.10.0.1/24' and server.get('ListenPort')=='51840'
 assert not any(k in row for row in rows for k in ['SaveConfig','PreUp','PostUp','PreDown','PostDown'])
 stripped=run(['/usr/bin/awg-quick','strip',str(CONFIG)]).stdout
 expected=parts(stripped);assert len(expected)>50
 enabled=run(['systemctl','is-enabled',UNIT],False).stdout.strip()
 assert enabled in ('disabled','enabled')
 root=Path('/root')/('awg-boot-recovery-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));root.mkdir(mode=0o700)
 shutil.copy2(CONFIG,root/'awg-test.conf')
 with tarfile.open(root/'client-profiles.tar.gz','w:gz') as archive:archive.add('/opt/vpn-manager/awg-clients',arcname='awg-clients')
 db=Path('/opt/vpn-manager/data/clients-staging.db')
 with sqlite3.connect(root/'clients.db') as dest,sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src:src.backup(dest)
 for cmd,name in [(['iptables-save','-c'],'iptables.txt'),(['ip','-j','route','show','table','all'],'routes.json'),(['ip','-j','rule','show'],'rules.json')]:
  (root/name).write_text(run(cmd).stdout)
 missing=[rule for rule in RULES if run(iptables(rule,'-C'),False).returncode!=0]
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with opener.open('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+HELPER_RELEASE+'/deploy/awgnetwork.py',timeout=30) as response:code=response.read()
 assert blob(code)==HELPER_BLOB;compile(code,'awgnetwork.py','exec')
 profiles={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/opt/vpn-manager/awg-clients').glob('*/client.conf')}
 before_xray=run(['systemctl','show','xray.service','-p','MainPID','--value']).stdout
 before_route=run(['ip','-j','route','get','1.1.1.1']).stdout
 rollback_code="""from pathlib import Path
import subprocess
def run(args):subprocess.run(args,capture_output=True,timeout=45)
run(['systemctl','stop',UNIT])
if Path('/sys/class/net/awg-test').exists():run(['ip','link','delete','dev','awg-test'])
for rule in MISSING:
 flag,table,chain,*match=rule
 args=['/usr/sbin/iptables','-w','5',flag,table]
 if subprocess.run(args+['-C',chain]+match,capture_output=True).returncode==0:run(args+['-D',chain]+match)
DROP.unlink(missing_ok=True);HELPER.unlink(missing_ok=True)
if OLD_ENABLED=='disabled':run(['systemctl','disable',UNIT])
run(['systemctl','daemon-reload'])
print('AWG boot recovery rolled back; existing database and profile files retained')
"""
 preamble='from pathlib import Path\\nUNIT='+repr(UNIT)+'\\nDROP=Path('+repr(str(DROP))+')\\nHELPER=Path('+repr(str(HELPER))+')\\nOLD_ENABLED='+repr(enabled)+'\\nMISSING='+repr(missing)+'\\n'
 # Store an executable rollback before any service/firewall changes.
 rollback=root/'rollback.py';rollback.write_text(preamble.replace('\\n','\n')+rollback_code);os.chmod(rollback,0o700)
 compile(rollback.read_text(),str(rollback),'exec')
 timer='vpn-awg-boot-rollback-'+root.name.rsplit('-',1)[-1].lower()
 run(['systemd-run','--unit='+timer,'--on-active=180s','/usr/bin/python3',str(rollback)])
 phase='install_startup'
 try:
  HELPER.parent.mkdir(parents=True,exist_ok=True);write(HELPER,code)
  DROP.parent.mkdir(parents=True,exist_ok=True)
  override=b'[Unit]\nWants=network-online.target\nAfter=network-online.target docker.service\n[Service]\nExecStartPre=/usr/sbin/modprobe amneziawg\nExecStart=\nExecStart=/usr/bin/awg-quick up /etc/amnezia/amneziawg/awg-test.conf\nExecStop=\nExecStop=/usr/bin/awg-quick down /etc/amnezia/amneziawg/awg-test.conf\nExecStartPost=/usr/bin/python3 /usr/local/libexec/vpn-manager-awg-network.py\n'
  write(DROP,override);run(['systemctl','daemon-reload'])
  phase='start_interface'
  result=run(['systemctl','start',UNIT],False)
  (root/'start.stdout').write_text(result.stdout);(root/'start.stderr').write_text(result.stderr)
  assert result.returncode==0 and Path('/sys/class/net/awg-test').exists()
  phase='verify_runtime'
  active=parts(run(['awg','showconf','awg-test']).stdout)
  assert active[0]['PrivateKey']==expected[0]['PrivateKey']
  assert {x['PublicKey'] for x in active[1:]}=={x['PublicKey'] for x in expected[1:]}
  for key in ['ListenPort','S1','S2','H1','H2','H3','H4']:assert active[0].get(key)==expected[0].get(key)
  assert all(run(iptables(rule,'-C'),False).returncode==0 for rule in RULES)
  phase='verify_preservation'
  assert CONFIG.read_bytes()==config
  assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in profiles.items())
  assert run(['systemctl','show','xray.service','-p','MainPID','--value']).stdout==before_xray
  assert run(['ip','-j','route','get','1.1.1.1']).stdout==before_route
  run(['systemctl','enable',UNIT]);assert run(['systemctl','is-enabled',UNIT]).stdout.strip()=='enabled'
  run(['systemctl','stop',timer+'.timer'])
  print(json.dumps(dict(ok=True,interface='awg-test',restored_existing_peers=len(active)-1,boot_enabled=True,forwarding_and_nat_verified=True,profiles_and_keys_unchanged=True,xray_untouched=True,backup=str(root),rollback=str(rollback))))
 except Exception as error:
  (root/'failure-phase.json').write_text(json.dumps(dict(phase=phase,error=type(error).__name__)))
  run(['python3',str(rollback)],False);run(['systemctl','stop',timer+'.timer'],False)
  raise SystemExit('AWG recovery stopped and scoped changes rolled back; protected backup: '+str(root)) from None
if __name__=='__main__':
 try:main()
 except (AssertionError,RuntimeError):raise SystemExit('AWG recovery preflight stopped; no secret details printed') from None
