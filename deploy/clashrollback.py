"""Restore only Clash Mi portal release code and scoped HTTPS/service settings."""
from pathlib import Path
import json,os,subprocess,sys,tempfile
def main():
 backup=Path(sys.argv[1]).resolve()
 assert os.geteuid()==0 and backup.parent==Path('/root/vpn-manager-backups') and backup.name.startswith('clash-mi-')
 allowed={'/opt/vpn-manager-connect/'+name for name in ('connect.html','connect.js','portal_service.py','clash_export.py')}
 allowed.update(['/etc/caddy/Caddyfile','/etc/systemd/system/vpn-manager-connect.service.d/50-clash-pilot.conf'])
 manifest=json.loads((backup/'files.json').read_text())
 assert {x['path'] for x in manifest}==allowed
 for i,entry in enumerate(manifest):
  p=Path(entry['path']);assert not p.is_symlink()
  if entry['existed']:
   fd,n=tempfile.mkstemp(dir=p.parent)
   with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),entry['mode']);f.write((backup/str(i)).read_bytes());f.flush();os.fsync(f.fileno())
   os.replace(n,p)
  else:p.unlink(missing_ok=True)
 for command in [('systemctl','daemon-reload'),('systemctl','reload','caddy.service'),('systemctl','restart','vpn-manager-connect.service')]:
  subprocess.run(command,capture_output=True,check=True,timeout=40)
 print('Portal code restored; client databases, profile keys, AWG and Xray retained')
if __name__=='__main__':main()
