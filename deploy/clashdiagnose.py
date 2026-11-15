"""Private preflight diagnostics; no profile values are printed."""
import importlib.util,json,traceback
from pathlib import Path
spec=importlib.util.spec_from_file_location('release','/root/vpn-clashinstall.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
checks=[]
for source,target,old,new in m.FILES:
 p=Path(target);checks.append(dict(source=source,exists=p.exists(),expected_previous=(m.blob(p.read_bytes())==old) if p.exists() else old is None,already_new=(m.blob(p.read_bytes())==new) if p.exists() else False))
print(json.dumps(dict(files=checks,backups=[p.name for p in Path('/root/vpn-manager-backups').glob('clash-mi-*')])))
try:m.main()
except Exception as e:
 print(json.dumps(dict(error=type(e).__name__,frames=[dict(file=Path(f.filename).name,line=f.lineno) for f in traceback.extract_tb(e.__traceback__)],returncode=getattr(e,'returncode',None))))
