"""Read-only comparison of prepared state; never starts services or changes VPN."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime,timezone
import traceback

def main():
    path=Path('/root/promote-vpn-live.py')
    if hashlib.sha256(path.read_bytes()).hexdigest()!='bc97d1fc7309b3156c75682b0d12d14618210f71fb1809020b5bbee4bc93667e':
        raise SystemExit('Unexpected installer hash; no application code imported')
    spec=importlib.util.spec_from_file_location('promotion',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    sys.path.insert(0,str(module.CODE))
    for name,digest in module.FILES.items():
        if name.endswith('.py'):
            target=module.CODE/Path(name).name
            if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                raise SystemExit('Prepared code digest mismatch')
    result={}
    try:
        current,owner,peers=module.baseline(datetime.now(timezone.utc))
        saved=json.loads((module.STATE/'baseline.json').read_text())
        result['baseline_sections_equal']={key:current.get(key)==saved.get(key) for key in set(current)|set(saved)}
        result['clients']=len(current['clients'])
    except Exception as error:
        result['exception']=type(error).__name__
        result['frames']=[dict(file=Path(item.filename).name,line=item.lineno) for item in traceback.extract_tb(error.__traceback__)]
        if isinstance(error,RuntimeError) and str(error) in ('Original profile consistency failure','Expected exactly 59 original clients','Expired-client count changed; review before promotion','Review original profile ownership before promotion'):
            result['reason']=str(error)
    backup=Path('/root/vpn-manager-backups/live-promotion-20261113T072219Z')
    result['configuration_matches_pre_activation']=module.CONFIG.read_bytes()==(backup/'awg-test.conf').read_bytes()
    result['data_directory_mode']=oct(module.DB.parent.stat().st_mode & 0o777)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
