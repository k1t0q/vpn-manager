"""Resume only the exact reviewed, never-used prepared production package."""
import ast
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import traceback

EXPECTED='bc97d1fc7309b3156c75682b0d12d14618210f71fb1809020b5bbee4bc93667e'
def inherit_tls(text):
    from route_stage import clean_line
    lines=text.splitlines(True)
    starts=[i for i,line in enumerate(lines) if ':8443' in clean_line(line) and clean_line(line).rstrip().endswith('{')]
    if len(starts)!=1:raise RuntimeError('Cannot identify existing TLS site')
    depth=1;blocks=[];i=starts[0]+1
    while i<len(lines) and depth:
        clean=clean_line(lines[i]);stripped=clean.strip()
        if depth==1 and (stripped=='tls' or stripped.startswith('tls ')):
            first=i;balance=clean.count('{')-clean.count('}')
            while balance>0:
                i+=1
                if i>=len(lines):raise RuntimeError('Unbalanced TLS block')
                item=clean_line(lines[i]);balance+=item.count('{')-item.count('}')
            blocks.append(''.join(lines[first:i+1]))
        else:depth+=clean.count('{')-clean.count('}')
        i+=1
    if len(blocks)!=1:raise RuntimeError('Expected one explicit TLS policy')
    return blocks[0]

def main():
    if os.geteuid()!=0:raise SystemExit('Root required')
    os.umask(0o077)
    source=Path('/root/promote-vpn-live.py').read_text()
    if hashlib.sha256(source.encode()).hexdigest()!=EXPECTED:raise SystemExit('Installer digest mismatch')
    spec=importlib.util.spec_from_file_location('promotion','/root/promote-vpn-live.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    sys.path.insert(0,str(m.CODE))
    os.environ['NO_PROXY']='127.0.0.1,localhost'
    for name,digest in m.FILES.items():
        if name.endswith('.py'):
            path=m.CODE/Path(name).name
            if path.is_symlink() or path.stat().st_uid!=0 or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise SystemExit('Prepared code mismatch')
    if (m.STATE/'activation.json').exists() or (m.STATE/'operations/pending.json').exists() or (m.STAGE/'operations/pending.json').exists():raise SystemExit('Active or pending operation; refusing resume')
    for name in m.OLD:
        if m.run('systemctl','is-active',name)!='active':raise SystemExit('Expected legacy API is not active')
    for name in m.NEW:
        if m.run('systemctl','is-active',name,check=False) not in ('inactive','failed'):raise SystemExit('A new service is already active')
    with sqlite3.connect((m.AUTH/'sessions.db').as_uri()+'?mode=ro',uri=True) as con:
        if con.execute('SELECT count(*) FROM panel_sessions').fetchone()[0]:raise SystemExit('Prepared authentication was already used')
    for path in (m.STATE,m.CODE,m.SITE,m.AUTH):
        if path.is_symlink():raise SystemExit('Unexpected symlink')
    base,owner,live=m.baseline(datetime.now(timezone.utc))
    if base!=json.loads((m.STATE/'baseline.json').read_text()):raise SystemExit('Prepared baseline changed')
    previous=Path('/root/vpn-manager-backups/live-promotion-20261113T072219Z')
    if m.CONFIG.read_bytes()!=(previous/'awg-test.conf').read_bytes():raise SystemExit('VPN configuration changed')
    m.check_other_writers()
    if m.listeners((5055,5056,8444)):raise SystemExit('New port occupied')
    # Verify the frozen public app and its separate Python runtime as well.
    for name,digest in m.FILES.items():
        if name.startswith('server/') and name.endswith('.py'):path=m.SITE/'server'/Path(name).name
        elif '/' not in name:path=m.SITE/'public'/name
        else:continue
        if path.is_symlink() or path.stat().st_uid!=0 or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise SystemExit('Prepared API or asset mismatch')
    m.run('runuser','-u','vpn-panel','--',str(m.SITE/'venv/bin/python'),'-c','import flask,requests,gunicorn')
    import route_live
    route_live.ROUTES=route_live.ROUTES.replace('https://185-23-239-142.sslip.io:8444 {\n','https://185-23-239-142.sslip.io:8444 {\n'+inherit_tls(Path('/etc/caddy/Caddyfile').read_text()),1)
    # Validate the exact repaired TLS candidate before stopping any API.
    import tempfile
    original_caddy=route_live.CONFIG.read_bytes()
    route_live.SNIPPET.write_text(route_live.ROUTES);route_live.SNIPPET.chmod(0o644)
    fd,candidate_name=tempfile.mkstemp(dir=route_live.CONFIG.parent,prefix='.live-reviewed-');os.close(fd)
    candidate_path=Path(candidate_name)
    try:
        candidate_path.write_bytes(original_caddy.rstrip()+b'\n\nimport /etc/caddy/vpn-manager-live.caddy\n')
        route_live.verify_listeners(route_live.adapt(route_live.CONFIG),route_live.adapt(candidate_path))
        route_live.run('caddy','validate','--config',str(candidate_path),'--adapter','caddyfile')
    finally:
        candidate_path.unlink(missing_ok=True);route_live.SNIPPET.unlink(missing_ok=True)
    print('REPAIRED_TLS_PREFLIGHT_READY')
    for old in m.OLD:
        folder=Path('/etc/systemd/system',old+'.d');folder.mkdir(exist_ok=True)
        m.write(folder/'live-writer.conf',('[Unit]\nConditionPathExists=!'+str(m.STATE/'activation.json')+'\n').encode(),0o644)
    function=next(node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name=='activate')
    body=ast.get_source_segment(source,function)
    start=body.index('    prepare_directories()')
    end=body.index('    try:\n        for old in OLD:',start)
    body=body[:start]+"    token=Path('/etc/vpn-manager-live-bridge.env').read_text().strip().split('=',1)[1]\n"+body[end:]
    body=body.replace('    except Exception:\n        rollback(backup)',"""    except Exception as error:
        detail=dict(exception=type(error).__name__,frames=[dict(file=Path(item.filename).name,line=item.lineno) for item in __import__('traceback').extract_tb(error.__traceback__)])
        if isinstance(error,RuntimeError):detail['reason']=str(error)
        if isinstance(error,subprocess.CalledProcessError):
            detail['command']=error.cmd
            detail['exitcode']=error.returncode
            detail['stderr_categories']=[label for needle,label in [('Permission denied','permission_denied'),('not found','not_found'),('does not exist','missing_path'),('Invalid argument','invalid_argument')] if needle in (error.stderr or '')]
        write(backup/'activation-error.json',json.dumps(detail).encode())
        print(json.dumps(detail))
        rollback(backup)""")
    exec(compile(body,'reviewed-prepared-activation','exec'),m.__dict__)
    m.activate({},True)
if __name__=='__main__':main()

