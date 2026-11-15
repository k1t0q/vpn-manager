"""Read-only preview after promotion; never schedules or mutates VPN peers."""
from http.server import HTTPServer
import os
from pathlib import Path
from awg_persistence import StateError
from awg_provision import Provisioner
from awg_test_server import Handler


class SnapshotBridge:
    def __init__(self, store):
        self.store=store
    def dispatch(self, *args, **kwargs):
        raise StateError('Preview is read-only after production activation')


class SnapshotHandler(Handler):
    def handle_request(self):
        if self.path=='/api/managed/health':
            import hmac
            supplied=self.headers.get('X-VPN-Token','')
            if not supplied or not hmac.compare_digest(supplied,self.server.token):
                return self.reply(401,dict(ok=False,error='Unauthorized'))
            return self.reply(200,dict(ok=True,auto_expiry=False,subscriptions=False,
                                      scheduler_ok=False,managed_test_only=True,
                                      profile_delivery=False,profile_import=False,
                                      read_only=True,notification_delivery='panel-only'))
        return super().handle_request()


def main():
    root=Path('/var/lib/vpn-manager-awg-stage')
    store=Provisioner(root/'clients.db',Path('/etc/amnezia/amneziawg/awg-test.conf'),
                      root/'profiles',root/'operations',protected_ids=())
    if (store.state/'pending.json').exists():
        raise SystemExit('Resolve staging journal before snapshot startup')
    token=os.environ.get('PANEL_AWG_TOKEN','')
    if len(token)<32:raise SystemExit('Snapshot token missing')
    server=HTTPServer(('127.0.0.1',5054),SnapshotHandler)
    server.token=token;server.bridge=SnapshotBridge(store)
    server.serve_forever()


if __name__=='__main__':main()
