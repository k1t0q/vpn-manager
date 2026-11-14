"""Sanitize the last AWG startup failure without exposing config/key values."""
from pathlib import Path
import json,re,subprocess
backups=sorted(Path('/root').glob('awg-boot-recovery-*'));root=backups[-1]
r=subprocess.run(['journalctl','-u','awg-quick@awg-test.service','-b','-n','50','--no-pager','-o','cat'],capture_output=True,text=True,timeout=15)
text=r.stdout;(root/'journal.private.txt').write_text(text)
patterns=['Operation not supported','Protocol not supported','No such file or directory','already exists','Permission denied','Invalid argument','Line unrecognized','Failed to create','modprobe','RTNETLINK','ExecStartPost','not found','SyntaxError']
flags={p:p.lower() in text.lower() for p in patterns}
safe=[]
for line in text.splitlines():
 if any(word in line.lower() for word in ['key','token','secret']):continue
 if any(word in line for word in ['RTNETLINK','ip link','ip -','wg-quick:','awg-quick:','Main process exited','Failed with result','Failed to start','status=','SyntaxError','Traceback','File "']):
  safe.append(re.sub(r'[A-Za-z0-9+/]{32,}={0,2}','<redacted>',line))
print(json.dumps(dict(backup=str(root),classification=flags,safe_startup_messages=safe,interface_present=Path('/sys/class/net/awg-test').exists()),indent=1))
