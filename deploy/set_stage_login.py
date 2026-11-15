"""Owner-operated initial preview login setup. Secret entry uses /dev/tty without echo.
Never reads or changes production credentials. Refuses to reset an existing code.
"""
import getpass,os,subprocess,tempfile
from pathlib import Path

def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 path=Path('/etc/vpn-manager-stage.env')
 text=path.read_text()
 lines=text.splitlines()
 indices=[i for i,line in enumerate(lines) if line.startswith('PANEL_ADMIN_CODE=')]
 if len(indices)!=1:raise SystemExit('Unexpected preview configuration')
 index=indices[0]
 if lines[index]!='PANEL_ADMIN_CODE=':raise SystemExit('Preview login already configured; not modified')
 code=getpass.getpass('New staging admin code (16+ characters): ')
 confirm=getpass.getpass('Confirm staging admin code: ')
 if len(code)<16 or any(c in code for c in ('\n','\r','\0')) or code!=confirm:
  raise SystemExit('Codes must match and contain at least 16 characters. Nothing changed.')
 escaped=code.replace('\\','\\\\').replace('"','\\"')
 lines[index]='PANEL_ADMIN_CODE="'+escaped+'"'
 fd,name=tempfile.mkstemp(prefix='.vpn-stage-env-',dir='/etc')
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w') as f:
   f.write('\n'.join(lines)+'\n');f.flush();os.fsync(f.fileno())
  os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)
 result=subprocess.run(['systemctl','restart','vpn-manager-stage.service'],capture_output=True,timeout=30)
 if result.returncode:raise SystemExit('Code saved; preview restart needs inspection')
 print('STAGING_LOGIN_CONFIGURED; working VPN unchanged')

if __name__=='__main__':main()
