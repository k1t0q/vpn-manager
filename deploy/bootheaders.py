"""Locate existing AWG source and matching installed kernel headers."""
from pathlib import Path
import json,subprocess
kernel=subprocess.run(['uname','-r'],capture_output=True,text=True,check=True).stdout.strip()
out=dict(kernel=kernel,headers_link=str((Path('/lib/modules')/kernel/'build').resolve()),headers_available=(Path('/lib/modules')/kernel/'build/Makefile').exists(),source_candidates=[])
for base in ['/root','/opt','/usr/src']:
 for p in Path(base).iterdir():
  if p.is_dir() and any(x in p.name.lower() for x in ['amnezia','awg']):
   out['source_candidates'].append(dict(path=str(p),makefiles=[str(x) for x in p.glob('**/Makefile')][:10]))
r=subprocess.run(['dkms','status'],capture_output=True,text=True) if Path('/usr/sbin/dkms').exists() else None
out['dkms_status']=r.stdout.strip() if r else None
print(json.dumps(out,indent=1))
