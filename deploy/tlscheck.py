"""Validate candidate routing without reloading Caddy or starting services."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
def main():
    root=Path('/usr/local/libexec/vpn-manager-production')
    import sys;sys.path.insert(0,str(root))
    import route_live as route
    if route.SNIPPET.exists():raise SystemExit('Unexpected live snippet')
    original=route.CONFIG.read_bytes()
    route.SNIPPET.write_text(route.ROUTES);route.SNIPPET.chmod(0o644)
    fd,name=tempfile.mkstemp(dir=route.CONFIG.parent,prefix='.live-check-');os.close(fd)
    path=Path(name)
    try:
        path.write_bytes(original.rstrip()+b'\n\nimport /etc/caddy/vpn-manager-live.caddy\n')
        result=subprocess.run(['caddy','validate','--config',str(path),'--adapter','caddyfile'],capture_output=True,text=True)
        lines=result.stderr.splitlines()
        error=next((line for line in reversed(lines) if line.startswith('Error:')),'No plain error line')
        error=re.sub(r'[A-Za-z0-9_+/-]{28,}','<redacted-token-or-path>',error)
        print(json.dumps(dict(returncode=result.returncode,error=error)))
        # Only path references, never certificate/key contents or other directives.
        for line in original.decode().splitlines():
            if line.strip().startswith('tls '):
                fields=line.strip().split()
                print('EXISTING_TLS_DIRECTIVE: '+' '.join(fields[:3]))
    finally:
        path.unlink(missing_ok=True);route.SNIPPET.unlink(missing_ok=True)
if __name__=='__main__':main()
