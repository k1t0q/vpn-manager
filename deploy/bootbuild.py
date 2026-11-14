"""Rebuild the already installed AWG source for the current kernel; no VPN config writes."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,shutil,subprocess,tempfile
def run(args,timeout=30):return subprocess.run(args,capture_output=True,text=True,timeout=timeout)
def main():
 assert os.geteuid()==0;os.umask(0o077)
 assert not Path('/sys/class/net/awg-test').exists()
 kernel=run(['uname','-r']).stdout.strip();assert kernel=='5.15.0-203-generic'
 assert (Path('/lib/modules')/kernel/'build/Makefile').exists()
 source=Path('/root/amneziawg-linux-kernel-module/src');assert (source/'Makefile').exists()
 target=Path('/lib/modules')/kernel/'extra/amneziawg.ko';assert not target.exists()
 root=Path('/root')/('awg-driver-rebuild-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));root.mkdir(mode=0o700)
 stage=root/'src'
 shutil.copytree(source,stage,symlinks=True,ignore=shutil.ignore_patterns('.git','*.o','*.ko','*.cmd','.tmp_versions','Module.symvers','modules.order'))
 for original in source.rglob('*'):
  if original.is_symlink():
   copied=stage/original.relative_to(source)
   if copied.is_symlink():copied.unlink();copied.symlink_to(original.resolve(),target_is_directory=original.resolve().is_dir())
 old=Path('/lib/modules/5.15.0-73-generic/extra/amneziawg.ko')
 if old.exists():shutil.copy2(old,root/'previous-kernel-module.ko')
 print(json.dumps(dict(phase='building',kernel=kernel,backup=str(root))),flush=True)
 with (root/'build.log').open('w') as log:
  result=subprocess.run(['make','-C',str(stage),'-j2','KERNELRELEASE='+kernel],stdout=log,stderr=subprocess.STDOUT,timeout=300)
 if result.returncode:
  print(json.dumps(dict(ok=False,phase='build_failed',tail=(root/'build.log').read_text(errors='replace').splitlines()[-12:],log=str(root/'build.log'))));return
 module=stage/'amneziawg.ko';assert module.exists()
 vermagic=run(['modinfo','-F','vermagic',str(module)]).stdout.strip();assert vermagic.startswith(kernel+' ')
 target.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
 rollback=root/'rollback-module.py'
 rollback.write_text("from pathlib import Path\nimport subprocess\nassert not Path('/sys/class/net/awg-test').exists()\nsubprocess.run(['modprobe','-r','amneziawg'],check=True)\nPath("+repr(str(target))+").unlink(missing_ok=True)\nsubprocess.run(['depmod','-a',"+repr(kernel)+"],check=True)\n")
 fd,tmp=tempfile.mkstemp(dir=target.parent)
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o644);f.write(module.read_bytes());f.flush();os.fsync(f.fileno())
 os.replace(tmp,target)
 result=run(['depmod','-a',kernel]);assert result.returncode==0
 result=run(['modprobe','amneziawg'])
 (root/'load.log').write_text(result.stdout+result.stderr)
 if result.returncode:
  target.unlink();run(['depmod','-a',kernel])
  print(json.dumps(dict(ok=False,phase='module_load_failed',error=result.stderr.strip(),backup=str(root))));return
 assert Path('/sys/module/amneziawg').exists()
 print(json.dumps(dict(ok=True,module_loaded=True,kernel=kernel,vermagic=vermagic,configuration_unchanged=True,backup=str(root),rollback=str(rollback))),flush=True)
if __name__=='__main__':main()
