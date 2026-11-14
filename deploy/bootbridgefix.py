"""Repair only a vanished runtime-only baseline peer after the recorded reboot."""
import os,json,sys,hashlib,subprocess,tempfile,shutil
from pathlib import Path
from datetime import datetime,timezone
os.umask(0o077)
root=Path('/var/lib/vpn-manager-production')
basepath=root/'baseline.json'
sys.path.insert(0,'/usr/local/libexec/vpn-manager-production')
from awg_persistence import AWGRuntime,peer_blocks
from awg_managed_bridge import ManagedBridge
from awg_provision import Provisioner
runtime=AWGRuntime()
live=runtime.peers()
_,blocks=peer_blocks(Path('/etc/amnezia/amneziawg/awg-test.conf').read_text())
disk={k for k,f,b in blocks}
old=basepath.read_bytes();base=json.loads(old)
original={v['public_key'] for v in base['clients'].values()}
metadata=set()
profiles=Path('/opt/vpn-manager/awg-clients')
for p in profiles.glob('*/metadata.json'):
    metadata.add(json.loads(p.read_text())['public_key'])
missing=set(base['runtime_peers'])-original-live
if len(missing)!=1 or missing & (disk|metadata):
    raise SystemExit('Refused: discrepancy differs from the single vanished runtime-only peer')
new=dict(base);new['runtime_peers']=[k for k in base['runtime_peers'] if k not in missing]
activation=json.loads((root/'activation.json').read_text())
store=Provisioner(Path('/opt/vpn-manager/data/clients-staging.db'),Path('/etc/amnezia/amneziawg/awg-test.conf'),profiles,root/'operations',protected_ids=())
store.owner_id=activation['owner_id']
ManagedBridge(store,root/'template.conf',new).guard()
backup=Path('/root/vpn-manager-backups')/('bridge-boot-repair-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700)
(backup/'baseline.json').write_bytes(old)
def atomic(p,data):
    fd,name=tempfile.mkstemp(dir=p.parent,prefix='.'+p.name)
    with os.fdopen(fd,'wb') as out:
        out.write(data);out.flush();os.fsync(out.fileno())
    os.replace(name,p)
    fd=os.open(p.parent,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
if basepath.read_bytes()!=old:raise SystemExit('Concurrent baseline change')
before=hashlib.sha256(Path('/etc/amnezia/amneziawg/awg-test.conf').read_bytes()).hexdigest()
atomic(basepath,json.dumps(new).encode())
r=subprocess.run(['systemctl','restart','vpn-manager-live-bridge.service'],capture_output=True)
if r.returncode:
    atomic(basepath,old)
    raise SystemExit('Bridge restart failed; baseline restored')
print(json.dumps(dict(ok=True,retired_vanished_runtime_only_peers=1,all_managed_profile_invariants_passed=True,backup=str(backup),vpn_restarted=False)))
