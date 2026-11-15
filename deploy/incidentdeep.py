"""Deep read-only AWG consistency and host resource checks. Never prints key material."""
from pathlib import Path
import base64,hashlib,ipaddress,json,os,subprocess,time
def run(args,data=None):
 r=subprocess.run(args,input=data,capture_output=True,text=True,timeout=10,check=True)
 return r.stdout
def sections(text):
 result=[];current=None
 for raw in text.splitlines():
  line=raw.strip()
  if not line or line.startswith(('#',';')):continue
  if line.startswith('['):
   current=dict(section=line.strip('[]'));result.append(current)
  elif current is not None and '=' in line:
   key,value=line.split('=',1);current[key.strip()]=value.strip()
 return result
runtime=sections(run(['awg','showconf','awg-test']));interface=runtime[0]
peers={x['PublicKey']:x for x in runtime[1:] if x.get('section')=='Peer'}
disk=sections(Path('/etc/amnezia/amneziawg/awg-test.conf').read_text())
server_public=run(['awg','show','awg-test','public-key']).strip()
snap=json.loads(Path('/var/lib/vpn-manager-connect/material/portal.json').read_text())
checks=[];errors=[]
critical=['S1','S2','H1','H2','H3','H4']
for cid,entry in snap['clients'].items():
 row=dict(id=int(cid),enabled=bool(entry.get('enabled')))
 try:
  folder=Path('/opt/vpn-manager/awg-clients')/cid
  conf=sections((folder/'client.conf').read_text());client,peer=conf
  public=run(['awg','pubkey'],client['PrivateKey']+'\n').strip()
  meta=json.loads((folder/'metadata.json').read_text())
  row.update(server_key_matches=peer['PublicKey']==server_public,client_key_matches=meta.get('public_key')==public,
   endpoint_matches=peer.get('Endpoint')=='185.23.239.142:'+interface.get('ListenPort','51840'),
   obfuscation_matches=all(client.get(k)==interface.get(k) for k in critical),present_in_runtime=public in peers)
  if public in peers:
   actual=peers[public]
   row['preshared_key_matches']=peer.get('PresharedKey','')==actual.get('PresharedKey','')
   addresses=[ipaddress.ip_interface(x.strip()).ip for x in client['Address'].split(',')]
   allowed=[ipaddress.ip_network(x.strip()) for x in actual.get('AllowedIPs','').split(',')]
   row['address_allowed']=all(any(a.version==n.version and a in n for n in allowed) for a in addresses)
  row['expected_enabled_state']=row['enabled']==row['present_in_runtime']
  bad=[k for k,v in row.items() if k not in ('id','enabled','present_in_runtime') and v is False]
  if bad:errors.append(dict(id=int(cid),failed=bad))
 except Exception as e:errors.append(dict(id=int(cid),error=type(e).__name__))
 checks.append(row)
resources={}
for name,path in [('conntrack_count','/proc/sys/net/netfilter/nf_conntrack_count'),('conntrack_max','/proc/sys/net/netfilter/nf_conntrack_max')]:
 p=Path(path)
 if p.exists():resources[name]=int(p.read_text().strip())
resources['loadavg']=Path('/proc/loadavg').read_text().split()[:3]
resources['memory_kb']={line.split(':')[0]:line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemAvailable:','MemTotal:'))}
netstats=Path('/proc/net/netstat').read_text().splitlines();tcp={}
for i in range(0,len(netstats)-1,2):
 keys=netstats[i].split();vals=netstats[i+1].split()
 if keys[0]=='TcpExt:':
  tcp={k:int(v) for k,v in zip(keys[1:],vals[1:]) if k in ['ListenOverflows','ListenDrops','TCPBacklogDrop','TCPReqQFullDoCookies','TCPReqQFullDrop']}
root=Path(Path('/root/vpn-incident-latest').read_text().strip())
result=dict(checked_clients=len(checks),running_peers=len(peers),enabled_snapshot=sum(x['enabled'] for x in checks),snapshot_age_seconds=int(time.time()-snap['updated_at']),
 errors=errors,pilot92=next((x for x in checks if x['id']==92),None),
 disk_runtime_private_key_match=disk[0].get('PrivateKey')==interface.get('PrivateKey'),
 disk_runtime_critical_parameters_match=all(disk[0].get(k)==interface.get(k) for k in critical),
 resources=resources,tcp_cumulative_counters=tcp)
(root/'deep-check.json').write_text(json.dumps(result))
print(json.dumps(result,indent=1))
