"""One-use production peer for an externally generated diagnostic client."""
import os,sys,json,re,time,base64,hashlib,sqlite3,fcntl,subprocess,tempfile,struct
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
os.umask(0o077)
def run(*a,check=True):
 r=subprocess.run(a,capture_output=True,text=True,timeout=20)
 if check and r.returncode:raise RuntimeError('Command failed')
 return r.stdout.strip()
root=Path('/var/lib/vpn-manager-production/operations')
config=Path('/etc/amnezia/amneziawg/awg-test.conf');profiles=Path('/opt/vpn-manager/awg-clients')
runid=sys.argv[-1]
if not runid.isdigit() or not 8<=len(runid)<=20:raise SystemExit('Invalid run ID')
backup=Path('/root/vpn-manager-backups')/('external-awg-'+runid)
unit='vpn-external-clean-'+runid;captureunit='vpn-external-capture-'+runid
folder=Path('/opt/vpn-manager-production/public/diagnostics')
public=folder/('external-'+runid+'.json')
def snap():
 return dict(config=hashlib.sha256(config.read_bytes()).hexdigest(),
 profiles={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in profiles.glob('*/client.conf')},
 peers=sorted(run('awg','show','awg-test','peers').split()),
 routes=run('ip','-j','route','show','table','all'),
 rules=run('ip','-j','rule','show'),
 xray=run('systemctl','show','xray','-p','MainPID','--value'))
def packet_counts():
 p=backup/'external.private.pcap';data=p.read_bytes() if p.exists() else b''
 ins=outs=0
 if len(data)>=24:
  en='<' if data[:4]==b'\xd4\xc3\xb2\xa1' else '>'
  link=struct.unpack(en+'I',data[20:24])[0];off=24
  while off+16<=len(data):
   _,_,size,_=struct.unpack(en+'IIII',data[off:off+16]);pkt=data[off+16:off+16+size];off+=16+size
   start=14 if link==1 else 16 if link==113 else 0
   if len(pkt)<start+28 or pkt[start]>>4!=4:continue
   ip=pkt[start:];ihl=(ip[0]&15)*4
   if ip[9]!=17 or len(ip)<ihl+8:continue
   sp,dp=struct.unpack('!HH',ip[ihl:ihl+4])
   if dp==51840 and ip[16:20]==bytes([185,23,239,142]):ins+=1
   if sp==51840 and ip[12:16]==bytes([185,23,239,142]):outs+=1
 return dict(incoming=ins,outgoing=outs)
def setup():
 if backup.exists():raise RuntimeError('Run already initialized')
 if run('awg','show','awg-test','listen-port')!='51840':raise RuntimeError('Unexpected live port')
 data=json.loads(Path('/root/vpn-external-public.json').read_text())
 if data.get('run_id')!=runid or not 0<=time.time()-data['created_at']<600:raise RuntimeError('Stale or mismatched public metadata')
 key=data['client_public']
 if len(base64.b64decode(key,validate=True))!=32:raise RuntimeError('Invalid disposable public key')
 encryption_key=serialization.load_der_public_key(base64.b64decode(data['encryption_public'],validate=True))
 if not isinstance(encryption_key,rsa.RSAPublicKey) or encryption_key.key_size<2048:raise RuntimeError('Invalid encryption key')
 address='10.10.0.254'
 import ipaddress
 with open(root/'lock','a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if (root/'pending.json').exists():raise RuntimeError('Pending production operation')
  before=snap()
  if key in before['peers']:raise RuntimeError('Public key collision')
  for p in profiles.glob('*/metadata.json'):
   if json.loads(p.read_text()).get('address')==address:raise RuntimeError('Test address occupied')
  for line in run('awg','show','awg-test','allowed-ips').splitlines():
   for value in line.split()[1:]:
    for net in value.split(','):
     if net!='(none)' and ipaddress.ip_address(address) in ipaddress.ip_network(net,strict=False):raise RuntimeError('Test address overlaps existing peer')
  if folder.exists() and (folder.is_symlink() or folder.stat().st_uid!=0):raise RuntimeError('Unsafe delivery directory')
  if public.exists():raise RuntimeError('Delivery file already exists')
  backup.mkdir(mode=0o700)
  (backup/'awg-test.conf').write_bytes(config.read_bytes())
  (backup/'invariants.json').write_text(json.dumps(before))
  db=Path('/opt/vpn-manager/data/clients-staging.db')
  with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as con:
   with sqlite3.connect(backup/'clients.db') as dest:
    con.backup(dest)
    if dest.execute('pragma integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity failure')
  cleanup=backup/'rollback.py'
  cleanup.write_text('import subprocess,fcntl,os\n'+
   'with open('+repr(str(root/'lock'))+',"a") as f:\n'+
   ' fcntl.flock(f,fcntl.LOCK_EX)\n'+
   ' subprocess.run(["awg","set","awg-test","peer",'+repr(key)+',"remove"],capture_output=True,timeout=15)\n'+
   ' try:os.unlink('+repr(str(public))+')\n'+
   ' except FileNotFoundError:pass\n'+
   'subprocess.run(["systemctl","stop",'+repr(captureunit+'.service')+'],capture_output=True,timeout=15)\n')
  cleanup.chmod(0o700)
  run('systemd-run','--quiet','--unit',unit,'--on-active=600s','/usr/bin/python3',str(cleanup))
  (backup/'record.json').write_text(json.dumps(dict(public_key=key,address=address,expires=int(time.time())+600)))
  try:
   psk=run('awg','genpsk')
   peer=backup/'temporary-peer.conf'
   peer.write_text('[Peer]\nPublicKey = '+key+'\nPresharedKey = '+psk+'\nAllowedIPs = '+address+'/32\n')
   run('awg','addconf','awg-test',str(peer))
   settings=[]
   for line in config.read_text().split('[Peer]')[0].splitlines():
    if '=' in line:
     k,v=map(str.strip,line.split('=',1))
     if re.fullmatch(r'Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5]',k,re.I):settings.append(k+' = '+v)
   bundle=dict(run_id=runid,expires=int(time.time())+480,address=address,endpoint='185.23.239.142:51840',
     server_public=run('awg','show','awg-test','public-key'),preshared=psk,obfuscation='\n'.join(settings))
   aes=os.urandom(32);nonce=os.urandom(12)
   envelope=dict(run_id=runid,
    wrapped_key=base64.b64encode(encryption_key.encrypt(aes,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))).decode(),
    nonce=base64.b64encode(nonce).decode(),ciphertext=base64.b64encode(AESGCM(aes).encrypt(nonce,json.dumps(bundle).encode(),runid.encode())).decode())
   run('systemd-run','--quiet','--unit',captureunit,'--property=RuntimeMaxSec=180','--property=UMask=0077',
    '/usr/bin/tcpdump','-Z','root','-i','eth0','-nn','-s','128','-U','-w',str(backup/'external.private.pcap'),'udp','port','51840')
   folder.mkdir(mode=0o755,exist_ok=True)
   fd,name=tempfile.mkstemp(dir=folder)
   with os.fdopen(fd,'w') as out:
    json.dump(envelope,out);out.flush();os.fsync(out.fileno())
   os.chmod(name,0o644);os.replace(name,public)
   print(json.dumps(dict(state='EXTERNAL_TEST_READY',run_id=runid,auto_cleanup_seconds=600,backup=str(backup))))
  except Exception:
   run('awg','set','awg-test','peer',key,'remove',check=False)
   public.unlink(missing_ok=True)
   raise
def finish():
 record=json.loads((backup/'record.json').read_text());key=record['public_key']
 result={'run_id':runid}
 lines=run('awg','show','awg-test','latest-handshakes').splitlines()
 result['server_observed_test_handshake']=any(l.split()[0]==key and int(l.split()[-1])>0 for l in lines)
 lines=run('awg','show','awg-test','transfer').splitlines()
 result['test_peer_transfer_bytes']=[list(map(int,l.split()[1:])) for l in lines if l.split()[0]==key]
 run('/usr/bin/python3',str(backup/'rollback.py'))
 run('systemctl','stop',unit+'.timer',check=False)
 before=json.loads((backup/'invariants.json').read_text());after=snap()
 result.update(production_invariants_unchanged=before==after,temporary_peer_removed=key not in after['peers'],encrypted_delivery_removed=not public.exists(),capture_packets=packet_counts())
 print(json.dumps(result))
try:
 if len(sys.argv)==3 and sys.argv[1]=='cleanup':finish()
 elif len(sys.argv)==2:setup()
 else:raise RuntimeError('Invalid arguments')
except Exception as e:
 print(json.dumps(dict(error_class=type(e).__name__,run_id=runid)))
 raise SystemExit(1)
