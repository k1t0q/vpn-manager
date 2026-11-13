import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

DEPLOY=Path(__file__).resolve().parents[1]/'deploy'
sys.path.insert(0,str(DEPLOY))
import promote_live as promotion
from staging_api import create_app


class PromotionTests(unittest.TestCase):
    def test_health_waits_for_scheduler_instead_of_accepting_early_ok(self):
        def response(value):
            item=Mock();item.__enter__=Mock(return_value=item);item.__exit__=Mock(return_value=False)
            item.read=Mock(return_value=json.dumps(value).encode());return item
        with patch.object(promotion,'urlopen',side_effect=[response({'ok':True,'scheduler_ok':False}),response({'ok':True,'scheduler_ok':True})]) as opened,patch.object(promotion.time,'sleep'):
            self.assertTrue(promotion.health('http://127.0.0.1:5055/health',require_scheduler=True)['scheduler_ok'])
            self.assertEqual(opened.call_count,2)
    def test_code_rollback_does_not_restore_databases_profiles_or_awg_configuration(self):
        plan=json.dumps(dict(old_enabled={unit:'enabled' for unit in promotion.OLD},caddy_mode=0o644))
        with patch.object(promotion,'run') as run,patch.object(promotion,'write') as write,patch.object(Path,'read_text',return_value=plan),patch.object(Path,'read_bytes',return_value=b'old caddy'),patch.object(Path,'unlink') as unlink:
            promotion.rollback(Path('/root/vpn-manager-backups/live-promotion-20261113T080000Z'))
        self.assertEqual([str(c.args[0]) for c in write.call_args_list],['/etc/caddy/Caddyfile'])
        commands=[c.args for c in run.call_args_list]
        self.assertFalse(any(c[0] in ('awg','awg-quick','wg-quick','xray') for c in commands))
        self.assertFalse(any(c[:2]==('systemctl','restart') for c in commands))
        self.assertTrue(all('/opt/vpn-manager/awg-clients' not in str(c) for c in unlink.call_args_list))
    def test_new_unit_orders_after_awg_but_does_not_start_or_require_it(self):
        text=promotion.unit('Production','/usr/bin/python3 /root/frozen.py',env='/etc/token.env',rw='/var/lib/private',after='network.target awg-quick@awg-test.service')
        self.assertIn('After=network.target awg-quick@awg-test.service',text)
        self.assertNotIn('Wants=',text);self.assertNotIn('Requires=',text)
        self.assertNotIn('ExecStart=/usr/bin/awg',text)
    def test_unprivileged_production_deletion_uses_canonical_billing_id(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'sessions.db';sqlite3.connect(db).close()
            app=create_app(dict(TESTING=True,DB_PATH=str(db),ADMIN_CODE='test-secret-code',ORIGIN='https://example.test',MANAGED_AWG=True,TEST_ONLY=False,AWG_URL='http://127.0.0.1:5055',AWG_TOKEN='fake-bridge-token'))
            client=app.test_client();token=client.post('/auth/login',json={'code':'test-secret-code'}).json['access_token']
            answer=Mock(content=b'{"ok":true}',status_code=200)
            with patch('staging_api.requests.post',return_value=answer) as post:
                result=client.post('/api/clients/42/delete',headers={'Authorization':'Bearer '+token,'Idempotency-Key':'delete-canonical-0001'})
            self.assertEqual(result.status_code,200)
            self.assertEqual(post.call_args.args[0],'http://127.0.0.1:5055/api/managed/clients/42/delete')
    def test_snapshot_rejects_all_mutation_dispatch_without_touching_store(self):
        from preview_snapshot import SnapshotBridge
        from awg_persistence import StateError
        store=Mock();bridge=SnapshotBridge(store)
        for path in ('/api/managed/create','/api/wg/client/1/disable','/api/managed/clients/1/edit'):
            with self.assertRaises(StateError):bridge.dispatch('POST',path,{},'test-request-0001')
        self.assertEqual(store.mock_calls,[])
    def test_baseline_freezes_59_keys_and_five_existing_expiry_holds(self):
        import awg_provision_test as fixture
        from datetime import datetime,timezone
        from awg_persistence import AWGRuntime
        fixture.ProvisionTests.setUp(self)
        try:
            with sqlite3.connect(self.db) as con:
                con.execute('DELETE FROM clients WHERE id=60')
                con.execute("UPDATE clients SET end_date='2026-01-01' WHERE id<=5")
            before=self.config.read_bytes()
            with patch.object(promotion,'DB',self.db),patch.object(promotion,'PROFILES',self.profiles),patch.object(promotion,'CONFIG',self.config),patch('awg_persistence.AWGRuntime',return_value=self.runtime),patch.object(promotion,'run',return_value='51840'):
                base,owner,live=promotion.baseline(datetime(2026,11,13,tzinfo=timezone.utc))
                self.assertEqual(len(base['clients']),59)
                self.assertEqual(set(base['initial_expiry_hold']),{'1','2','3','4','5'})
                self.assertEqual(owner,'owner');self.assertIn('unmanaged-test-peer',live)
                with sqlite3.connect(self.db) as con:con.execute("UPDATE clients SET end_date='2026-01-01' WHERE id=6")
                with self.assertRaisesRegex(RuntimeError,'Expired-client count changed'):
                    promotion.baseline(datetime(2026,11,13,tzinfo=timezone.utc))
            self.assertEqual(self.config.read_bytes(),before);self.assertEqual(self.runtime.calls,[])
        finally:fixture.ProvisionTests.tearDown(self)
