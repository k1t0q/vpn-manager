"""Locate installed AWG implementation and inspect startup code, without reading credentials."""
from pathlib import Path
import json,os,re,shutil,subprocess
out=dict(paths={x:shutil.which(x) for x in ['ip','awg','awg-quick','amneziawg-go','amneziawg-go-v1','wireguard-go']})
out['loaded_vpn_modules']=[line.split()[0] for line in Path('/proc/modules').read_text().splitlines() if any(x in line.lower() for x in ['amnezia','wireguard'])]
out['vpn_module_files']=[str(p) for p in Path('/lib/modules').rglob('*.ko*') if any(x in p.name.lower() for x in ['amnezia','wireguard'])]
out['vpn_binaries']=[str(p) for base in ['/usr/bin','/usr/local/bin','/usr/local/sbin','/opt','/root'] for p in Path(base).glob('*') if any(x in p.name.lower() for x in ['awg','amnezia']) and p.is_file()]
quick=Path('/usr/bin/awg-quick').read_text(errors='replace')
lines=quick.splitlines();selected=[]
for i,line in enumerate(lines):
 if any(x in line for x in ['add_if()', 'cmd()', 'auto_su()', 'HAVE_SET_DNS','add_default()', 'check_if_exists()']):
  selected.extend(lines[i:i+22])
out['quick_implementation_excerpt']=selected[:100]
out['ip_realpath']=str(Path(shutil.which('ip')).resolve())
print(json.dumps(out,indent=1))
