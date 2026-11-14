"""Scoped AWG firewall provisioning; no INPUT, OUTPUT, SSH or Xray changes."""
from pathlib import Path
import json,os,subprocess
RULES=[
 ['-t','filter','FORWARD','-i','eth0','-o','awg-test','-m','conntrack','--ctstate','RELATED,ESTABLISHED','-j','ACCEPT'],
 ['-t','filter','FORWARD','-i','awg-test','-o','eth0','-j','ACCEPT'],
 ['-t','nat','POSTROUTING','-s','10.10.0.0/24','-o','eth0','-j','MASQUERADE']]
def command(rule,operation):
 tableflag,table,chain,*matches=rule
 return ['/usr/sbin/iptables','-w','5',tableflag,table,operation,chain,*matches]
def main():
 assert os.geteuid()==0
 assert Path('/sys/class/net/awg-test').exists()
 assert Path('/proc/sys/net/ipv4/ip_forward').read_text().strip()=='1'
 added=[]
 for rule in RULES:
  if subprocess.run(command(rule,'-C'),capture_output=True).returncode:
   subprocess.run(command(rule,'-I' if rule[2]=='FORWARD' else '-A'),capture_output=True,check=True)
   added.append(rule)
 p=Path('/run/vpn-manager-awg-network-added.json')
 fd=os.open(p,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump(added,f)
if __name__=='__main__':main()
