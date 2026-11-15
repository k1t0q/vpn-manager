"""Short passive packet capture and boot inspection. Never changes network state."""
from pathlib import Path
import collections,json,os,struct,subprocess,time
root=Path(Path('/root/vpn-incident-latest').read_text().strip());os.umask(0o077)
pcap=root/('udp-'+str(int(time.time()))+'.pcap')
result=subprocess.run(['timeout','55','tcpdump','-i','eth0','-nn','-s','128','-U','-w',str(pcap),'udp port 51840'],capture_output=True,timeout=60)
incoming=outgoing=0;inflows=collections.Counter();outflows=collections.Counter()
data=pcap.read_bytes() if pcap.exists() else b''
if len(data)>=24:
 endian='<' if data[:4]==b'\xd4\xc3\xb2\xa1' else '>'
 link=struct.unpack(endian+'I',data[20:24])[0];offset=24
 while offset+16<=len(data):
  _,_,caplen,_=struct.unpack(endian+'IIII',data[offset:offset+16]);packet=data[offset+16:offset+16+caplen];offset+=16+caplen
  start=14 if link==1 else 16 if link==113 else 0
  if len(packet)<start+28 or packet[start]>>4!=4:continue
  ip=packet[start:];ihl=(ip[0]&15)*4
  if ip[9]!=17 or len(ip)<ihl+8:continue
  src,dst=ip[12:16],ip[16:20];sp,dp=struct.unpack('!HH',ip[ihl:ihl+4])
  if dst==bytes([185,23,239,142]) and dp==51840:incoming+=1;inflows[(src,sp)]+=1
  elif src==bytes([185,23,239,142]) and sp==51840:outgoing+=1;outflows[(dst,dp)]+=1
print(json.dumps(dict(udp_capture_seconds=55,incoming_packets=incoming,outgoing_packets=outgoing,remote_sources=len(inflows),sources_with_replies=len(set(inflows)&set(outflows)),packet_backup=str(pcap))))
boot=[]
for base in ['/etc/systemd/system','/lib/systemd/system']:
 for file in Path(base).glob('*.service'):
  try:
   if 'awg-test' in file.read_text(errors='replace'):
    state=subprocess.run(['systemctl','is-enabled',file.name],capture_output=True,text=True,timeout=5)
    boot.append(dict(unit=file.name,enabled=state.stdout.strip()))
  except OSError:pass
print('AWG_BOOT_UNITS '+json.dumps(boot))
for file in ['/etc/rc.local','/etc/crontab','/var/spool/cron/crontabs/root']:
 p=Path(file)
 if p.exists():print(json.dumps(dict(file=file,references_awg_test='awg-test' in p.read_text(errors='replace'))))
r=subprocess.run(['awg','show','awg-test','latest-handshakes'],capture_output=True,text=True,timeout=5)
times=[int(line.split()[-1]) for line in r.stdout.splitlines() if line.split()[-1].isdigit()]
print('HANDSHAKES '+json.dumps(dict(peers=len(times),recent_180s=sum(t>time.time()-180 for t in times),newest_age_seconds=int(time.time()-max(times)) if times and max(times)>0 else None)))
