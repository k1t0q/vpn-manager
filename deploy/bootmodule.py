"""Read-only kernel/tool and systemd compatibility diagnostics."""
from pathlib import Path
import json,subprocess
def run(args):
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=15);return dict(code=r.returncode,out=r.stdout.strip(),err=r.stderr.strip())
 except Exception as e:return dict(error=type(e).__name__)
out={name:run(args) for name,args in {
 'kernel':['uname','-r'],'awg_version':['/usr/bin/awg','--version'],
 'module_file':['modinfo','-F','filename','amneziawg'],
 'module_version':['modinfo','-F','version','amneziawg'],
 'module_vermagic':['modinfo','-F','vermagic','amneziawg'],
 'unit_sandbox':['systemctl','show','awg-quick@awg-test.service','-p','CapabilityBoundingSet','-p','RestrictAddressFamilies','-p','SystemCallFilter','-p','PrivateNetwork','-p','PrivateUsers']
}.items()}
journal=run(['journalctl','-u','awg-quick@awg-test.service','-b','-n','60','--no-pager','-o','cat']).get('out','')
out['safe_trace']=[line for line in journal.splitlines() if not any(x in line.lower() for x in ['key','token','secret']) and (line.startswith('[#]') or any(x in line for x in ['Protocol not supported','Unable to','Operation not supported','RTNETLINK','modprobe']))]
out['awg_realpath']=str(Path('/usr/bin/awg').resolve())
print(json.dumps(out,indent=1))
