"""One-use external AWG client. Publishes only disposable public keys."""
import os,time,json,base64,subprocess,tempfile,urllib.request,urllib.error,signal
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa,padding,x25519
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
os.umask(0o077)
runid=os.environ['GITHUB_RUN_ID']
repo=os.environ['GITHUB_REPOSITORY'];branch=os.environ['GITHUB_REF_NAME']
def run(*a,check=True):
 r=subprocess.run(a,capture_output=True,text=True,timeout=18)
 if check and r.returncode:raise RuntimeError('Command failed')
 return r
priv=x25519.X25519PrivateKey.generate()
raw=priv.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
pub=priv.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
rsa_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
public=dict(run_id=runid,created_at=int(time.time()),client_public=base64.b64encode(pub).decode(),
 encryption_public=base64.b64encode(rsa_key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).decode())
path='diagnostics/external-public-'+runid+'.json'
payload=json.dumps(dict(message='Publish disposable external-test public keys',branch=branch,content=base64.b64encode(json.dumps(public).encode()).decode())).encode()
req=urllib.request.Request('https://api.github.com/repos/'+repo+'/contents/'+path,data=payload,method='PUT',
 headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=20) as response:response.read()
print(json.dumps(dict(state='AWAITING_ENCRYPTED_SERVER_PARAMETERS',run_id=runid)),flush=True)
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
bundle=None
deadline=time.monotonic()+480
while time.monotonic()<deadline:
 try:
  with op.open('https://185-23-239-142.sslip.io:8444/panel-live/diagnostics/external-'+runid+'.json',timeout=8) as r:
   envelope=json.loads(r.read(20000))
  if envelope.get('run_id')!=runid:raise ValueError('Mismatched run')
  key=rsa_key.decrypt(base64.b64decode(envelope['wrapped_key']),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
  bundle=json.loads(AESGCM(key).decrypt(base64.b64decode(envelope['nonce']),base64.b64decode(envelope['ciphertext']),runid.encode()))
  if bundle['run_id']!=runid or bundle['expires']<time.time() or bundle['endpoint']!='185.23.239.142:51840':raise ValueError('Invalid parameters')
  break
 except (OSError,ValueError,KeyError,urllib.error.HTTPError):
  time.sleep(3)
if bundle is None:raise SystemExit('External test stopped: encrypted parameters not received')
temp=Path(tempfile.mkdtemp(prefix='vpn-external-',dir=os.environ.get('RUNNER_TEMP','/tmp')))
daemon=None;stage='start_userspace_client';result={'run_id':runid}
awg='/tmp/awg-tools/src/wg'
try:
 log=open(temp/'daemon.private.log','wb')
 daemon=subprocess.Popen(['/tmp/awg-go/amneziawg-go','-f','extdiag'],stdout=log,stderr=log)
 for _ in range(30):
  if Path('/var/run/amneziawg/extdiag.sock').exists():break
  if daemon.poll() is not None:raise RuntimeError('Userspace daemon exited')
  time.sleep(.2)
 conf='[Interface]\nPrivateKey = '+base64.b64encode(raw).decode()+'\n'+bundle['obfuscation']+'\n[Peer]\nPublicKey = '+bundle['server_public']+'\nPresharedKey = '+bundle['preshared']+'\nEndpoint = '+bundle['endpoint']+'\nAllowedIPs = 0.0.0.0/0\n'
 p=temp/'client.conf';p.write_text(conf);p.chmod(0o600)
 run(awg,'setconf','extdiag',str(p))
 run('ip','address','add',bundle['address']+'/32','dev','extdiag')
 run('ip','link','set','extdiag','mtu','1420','up')
 run('ip','route','add','10.10.0.1/32','dev','extdiag')
 run('ip','route','add','1.1.1.1/32','dev','extdiag')
 stage='external_handshake'
 ping=run('ping','-n','-c','4','-W','2','10.10.0.1',check=False)
 result['tunnel_ping_ok']=ping.returncode==0
 text=run(awg,'show','extdiag','latest-handshakes').stdout
 result['handshake_ok']=any(int(l.split()[-1])>0 for l in text.splitlines())
 text=run(awg,'show','extdiag','transfer').stdout
 result['transfer_bytes']=[list(map(int,l.split()[1:])) for l in text.splitlines()]
 if result['handshake_ok']:
  stage='external_internet'
  ping=run('ping','-n','-c','2','-W','2','1.1.1.1',check=False)
  result['internet_ping_ok']=ping.returncode==0
  try:
   with op.open('https://1.1.1.1/cdn-cgi/trace',timeout=8) as response:
    data=dict(l.split('=',1) for l in response.read().decode().splitlines() if '=' in l)
    result['https_status']=response.status;result['egress_matches_vps']=data.get('ip')=='185.23.239.142'
  except Exception as e:result['https_error_class']=type(e).__name__
except Exception as e:result.update(error_class=type(e).__name__,failed_stage=stage)
finally:
 run('ip','link','delete','extdiag',check=False)
 if daemon:
  daemon.terminate()
  try:daemon.wait(timeout=5)
  except subprocess.TimeoutExpired:daemon.kill()
 import shutil
 shutil.rmtree(temp)
 print('EXTERNAL_AWG_RESULT '+json.dumps(result),flush=True)
