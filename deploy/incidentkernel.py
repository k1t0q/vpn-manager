"""Read-only kernel/socket/firewall diagnostics. No keys or raw configuration output."""
import json,subprocess,re,os
from pathlib import Path
def run(*args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=15)
 return r.returncode,r.stdout.strip()
d={}
d['kernel']=run('uname','-r')[1]
d['interface']=json.loads(run('ip','-j','-d','link','show','awg-test')[1] or '[]')
d['udp_sockets']=[l for l in run('ss','-H','-lun')[1].splitlines() if ':51840' in l]
d['namespaces']=run('ip','netns','list')[1]
d['route']=run('ip','-4','route','get','1.1.1.1')[1]
d['rules']=json.loads(run('ip','-j','rule','show')[1] or '[]')
d['input']=run('iptables','-nvL','INPUT')[1]
d['output']=run('iptables','-nvL','OUTPUT')[1]
d['legacy']=run('iptables-legacy','-nvL','INPUT')[1]
d['module_versions']={}
for name in ['amneziawg','/lib/modules/5.15.0-73-generic/extra/amneziawg.ko']:
 d['module_versions'][name]={f:run('modinfo','-F',f,name)[1] for f in ['version','srcversion','vermagic']}
code,out=run('nft','-j','list','ruleset')
if not code:
 data=json.loads(out)['nftables']
 d['nft_hooks']=[x['chain'] for x in data if 'chain' in x and 'hook' in x['chain']]
 d['nft_drops']=[x['rule'] for x in data if 'rule' in x and any('drop' in e or 'reject' in e for e in x['rule'].get('expr',[]))]
for name in ['eth0','lo']:
 d['tc_'+name]=run('tc','filter','show','dev',name,'ingress')[1]
 d['link_'+name]=json.loads(run('ip','-j','-d','link','show',name)[1] or '[]')
d['udp_counters']={}
lines=Path('/proc/net/snmp').read_text().splitlines()
for a,b in zip(lines,lines[1:]):
 if a.startswith('Udp:') and b.startswith('Udp:'):
  d['udp_counters']=dict(zip(a.split()[1:],map(int,b.split()[1:])))
code,out=run('journalctl','-k','-b','-n','400','--no-pager','-o','cat')
d['kernel_problem_lines']=[l[:220] for l in out.splitlines() if re.search(r'amnezia|wireguard|invalid module|out of memory|NETDEV WATCHDOG|martian',l,re.I)]
# Only interface structural fields, no per-peer keys or endpoints.
for k in ['interface','link_eth0','link_lo']:
 d[k]=[{a:b for a,b in x.items() if a in ['ifname','flags','mtu','operstate','linkinfo','xdp']} for x in d[k]]
print(json.dumps({k:d[k] for k in ['kernel','interface','udp_sockets','namespaces','module_versions','input','output','legacy','nft_hooks','udp_counters']},separators=(',',':')))
