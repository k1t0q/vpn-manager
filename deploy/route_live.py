"""Add an isolated production HTTPS origin, preserving every old listener."""
from collections import Counter
from pathlib import Path
import json
import os
import tempfile
from route_stage import adapt,run,replace

CONFIG=Path('/etc/caddy/Caddyfile')
SNIPPET=Path('/etc/caddy/vpn-manager-live.caddy')
IMPORT='import /etc/caddy/vpn-manager-live.caddy'
ROUTES='''https://185-23-239-142.sslip.io:8444 {
    handle_path /panel-live/* {
        root * /opt/vpn-manager-production/public
        file_server
    }
    handle_path /live-api/* {
        reverse_proxy 127.0.0.1:5056
    }
}
'''


def listeners(config):
    return Counter(address for server in config.get('apps',{}).get('http',{}).get('servers',{}).values() for address in server.get('listen',[]))


def verify_listeners(before,after):
    old,new=listeners(before),listeners(after)
    if old-new:raise RuntimeError('Removal of an existing listener is forbidden')
    additions=new-old
    if not additions or any(not address.endswith(':8444') for address in additions):
        raise RuntimeError('Only the approved production listener 8444 may be added')


def main():
    if CONFIG.is_symlink() or SNIPPET.exists():raise RuntimeError('Live routing already exists or needs review')
    original=CONFIG.read_bytes();stat=CONFIG.stat();before=adapt(CONFIG)
    SNIPPET.write_text(ROUTES);SNIPPET.chmod(0o644)
    fd,name=tempfile.mkstemp(dir=CONFIG.parent,prefix='.live-candidate-');os.close(fd)
    trial=Path(name)
    try:
        candidate=original.decode().rstrip()+'\n\n'+IMPORT+'\n'
        trial.write_text(candidate)
        verify_listeners(before,adapt(trial))
        run('caddy','validate','--config',str(trial),'--adapter','caddyfile')
        replace(CONFIG,candidate.encode(),stat)
        try:run('caddy','reload','--config',str(CONFIG),'--adapter','caddyfile')
        except Exception:
            replace(CONFIG,original,stat)
            run('caddy','reload','--config',str(CONFIG),'--adapter','caddyfile')
            raise
    except Exception:
        SNIPPET.unlink(missing_ok=True)
        raise
    finally:trial.unlink(missing_ok=True)
    print(json.dumps(dict(ok=True,production_port=8444,old_listeners_preserved=True)))


if __name__=='__main__':main()
