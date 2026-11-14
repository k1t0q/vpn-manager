"""Check legacy netfilter, interface filters and SSH policy without changing anything."""
from pathlib import Path
import json,subprocess,time
def call(args):
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=10)
  return r.returncode,r.stdout
 except FileNotFoundError:return -1,''
result={}
for command in ['iptables-legacy-save','ip6tables-legacy-save']:
 code,out=call([command,'-c'])
 root=Path(Path('/root/vpn-incident-latest').read_text().strip());(root/(command+'.txt')).write_text(out)
 result[command]=dict(available=code==0,rule_count=sum(' -A ' in x or x.startswith('-A ') for x in out.splitlines()),drop_policies=[x.split()[0:2] for x in out.splitlines() if x.startswith(':') and ' DROP ' in x])
for direction in ['ingress','egress']:
 code,out=call(['tc','filter','show','dev','eth0',direction])
 result['tc_'+direction]=dict(code=code,filter_lines=len(out.strip().splitlines()) if out.strip() else 0)
code,out=call(['ip','-j','-d','link','show','dev','eth0'])
if code==0:
 link=json.loads(out)[0];result['xdp']=link.get('xdp',{});result['interface_mtu']=link.get('mtu')
code,out=call(['sshd','-T'])
allowed=['permitrootlogin','passwordauthentication','pubkeyauthentication','maxstartups','allowusers','denyusers','allowgroups','denygroups','listenaddress']
result['ssh_policy']=[line for line in out.splitlines() if line.split()[0] in allowed]
def stats():
 lines=Path('/proc/net/netstat').read_text().splitlines();output={}
 for i in range(0,len(lines)-1,2):
  names=lines[i].split();values=lines[i+1].split()
  if names[0]=='TcpExt:':output={k:int(v) for k,v in zip(names[1:],values[1:]) if k in ['ListenOverflows','ListenDrops','TCPBacklogDrop']}
 return output
start=stats();time.sleep(3);finish=stats();result['tcp_drop_delta_3s']={k:finish[k]-v for k,v in start.items()}
snapshot=json.loads(Path('/var/lib/vpn-manager-connect/material/portal.json').read_text());mapped=set()
for cid in snapshot['clients']:
 try:mapped.add(json.loads((Path('/opt/vpn-manager/awg-clients')/cid/'metadata.json').read_text())['public_key'])
 except Exception:pass
code,out=call(['awg','show','awg-test','peers']);extras=set(out.split())-mapped
locations=[]
for metadata in Path('/opt/vpn-manager/awg-clients').glob('*/metadata.json'):
 try:
  if json.loads(metadata.read_text()).get('public_key') in extras:locations.append(metadata.parent.name)
 except Exception:pass
result['runtime_peers_outside_portal_snapshot']=dict(count=len(extras),matching_profile_folder_ids=locations)
(root/'edge-check.json').write_text(json.dumps(result))
print(json.dumps(result,indent=1))
