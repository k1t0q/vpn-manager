"""Classify production bridge startup failure without printing credentials or requests."""
from pathlib import Path
import json,re,subprocess
unit='vpn-manager-live-bridge.service'
r=subprocess.run(['journalctl','-u',unit,'-b','-n','100','--no-pager','-o','cat'],capture_output=True,text=True,timeout=15)
root=Path(Path('/root/vpn-incident-latest').read_text().strip());(root/'bridge-journal.private.txt').write_text(r.stdout)
safe=[]
for line in r.stdout.splitlines():
 if any(word in line.lower() for word in ['token','secret','password','privatekey','presharedkey']):continue
 if line.strip().startswith('File "') or line.startswith('Traceback') or any(word in line for word in ['ModuleNotFoundError','ImportError','FileNotFoundError','PermissionError','OperationalError','RuntimeError','NameError','SyntaxError','KeyError','AttributeError','Failed at step','Failed to locate','Failed with result','Main process exited','status=']):
  safe.append(re.sub(r'[A-Za-z0-9+/]{32,}={0,2}','<redacted>',line))
show=subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','ExecMainStatus','-p','Result','-p','RuntimeDirectory','-p','StateDirectory'],capture_output=True,text=True,timeout=10)
print(json.dumps(dict(state=show.stdout.splitlines(),safe_failure=safe),indent=1))
