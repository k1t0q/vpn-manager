"""Locate installed AWG implementation and inspect startup code, without reading credentials."""
from pathlib import Path
import json,os,re,shutil,subprocess,sys
out=dict(paths={x:shutil.which(x) for x in ['ip','awg','awg-quick','amneziawg-go','amneziawg-go-v1','wireguard-go']})
out['loaded_vpn_modules']=[line.split()[0] for line in Path('/proc/modules').read_text().splitlines() if any(x in line.lower() for x in ['amnezia','wireguard'])]
out['vpn_module_files']=[str(p) for p in Path('/lib/modules').rglob('*.ko*') if any(x in p.name.lower() for x in ['amnezia','wireguard'])]
out['vpn_binaries']=[str(p) for base in ['/usr/bin','/usr/local/bin','/usr/local/sbin','/opt','/root'] for p in Path(base).glob('*') if any(x in p.name.lower() for x in ['awg','amnezia']) and p.is_file()]
quick=Path('/usr/bin/awg-quick').read_text(errors='replace')
lines=quick.splitlines();selected=[]
for i,line in enumerate(lines):
 if line.startswith(('cmd()', 'add_if()')):
  for item in lines[i:i+35]:
   selected.append(item)
   if item=='}':break
out['quick_implementation_excerpt']=selected
out['ip_realpath']=str(Path(shutil.which('ip')).resolve())
if len(sys.argv)>1 and sys.argv[1]=='quick':print('\n'.join(selected))
else:
 out.pop('quick_implementation_excerpt',None)
 print(json.dumps(out))
