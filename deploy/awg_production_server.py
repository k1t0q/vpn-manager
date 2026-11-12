"""Frozen production entry point. No installer or workflow starts this file yet."""
from http.server import HTTPServer
import json
import os
from pathlib import Path
import subprocess
import time
from awg_persistence import AWGRuntime
from awg_provision import Provisioner
from awg_managed_bridge import ManagedBridge
from awg_test_server import Handler

ROOT=Path('/var/lib/vpn-manager-production')


def main():
    marker=ROOT/'activation.json'
    if marker.is_symlink() or marker.stat().st_uid!=0 or marker.stat().st_mode & 0o077:
        raise SystemExit('An owner-approved private activation record is required')
    activation=json.loads(marker.read_text())
    if activation.get('approved') is not True or activation.get('original_clients')!=59:
        raise SystemExit('Production activation is not approved')
    result=subprocess.run(['systemctl','is-active','vpn-manager-awg-readonly.service'],capture_output=True,timeout=10)
    if result.stdout.strip() not in (b'inactive',b'failed'):
        raise SystemExit('Staging writer must be stopped before production activation')
    baseline=json.loads((ROOT/'baseline.json').read_text())
    store=Provisioner(Path('/opt/vpn-manager/data/clients-staging.db'),
                      Path('/etc/amnezia/amneziawg/awg-test.conf'),
                      Path('/opt/vpn-manager/awg-clients'),ROOT/'operations',protected_ids=())
    store.owner_id=activation['owner_id']
    runtime=AWGRuntime()
    runtime.peers()  # Do not silently enable offline control or start an interface.
    store.recover()
    bridge=ManagedBridge(store,ROOT/'template.conf',baseline)
    bridge.guard()
    token=os.environ.get('PANEL_AWG_TOKEN','')
    if len(token)<32:raise SystemExit('Production bridge token not configured')
    server=HTTPServer(('127.0.0.1',5055),Handler)
    server.token=token;server.bridge=bridge;server.timeout=1;server.test_only=False
    next_tick=0
    while True:
        server.handle_request()
        if time.monotonic()>=next_tick:
            try:bridge.tick();server.scheduler_ok=True
            except Exception:server.scheduler_ok=False
            next_tick=time.monotonic()+30


if __name__=='__main__':main()
