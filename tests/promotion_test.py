import importlib.util
import json
import os
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
    def test_profile_label_passes_proxy_without_exposing_profile_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'sessions.db';sqlite3.connect(db).close()
            app=create_app(dict(TESTING=True,DB_PATH=str(db),ADMIN_CODE='test-code',AWG_TOKEN='bridge-token',MANAGED_AWG=True))
            client=app.test_client();token=client.post('/auth/login',json={'code':'test-code'}).json['access_token']
            response=Mock(ok=True);response.json.return_value={'clients':[dict(id='legacy-id',client_id=7,name='Customer',profile_name='CAESAR BOT #42',private_key='DO_NOT_EXPOSE',preshared_key='DO_NOT_EXPOSE')]}
            with patch('staging_api.requests.request',return_value=response):
                result=client.get('/api/wg/clients',headers={'Authorization':'Bearer '+token})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json['clients'][0]['profile_name'],'CAESAR BOT #42')
            self.assertEqual(result.json['clients'][0]['client_id'],7)
            self.assertNotIn('DO_NOT_EXPOSE',result.get_data(as_text=True))

    def test_live_origin_inherits_exact_nested_existing_tls_policy(self):
        from resume import inherit_tls
        tls=' tls {\n  issuer acme {\n   email test@example.test\n  }\n }\n'
        text='https://example.test:8443 {\n'+tls+' handle {\n respond ok\n }\n}\n'
        self.assertEqual(inherit_tls(text),tls)
        with self.assertRaises(RuntimeError):inherit_tls(text+text)

    def test_restrictive_umask_keeps_state_private_but_allows_api_to_read_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.multiple(promotion,STATE=root/'state',CODE=root/'code',SITE=root/'site',AUTH=root/'auth'):
                old=os.umask(0o077)
                try:promotion.prepare_directories()
                finally:os.umask(old)
                for path in (root/'site',root/'site/server',root/'site/public'):
                    self.assertEqual(path.stat().st_mode & 0o777,0o755)
                for path in (root/'state',root/'code',root/'auth'):
                    self.assertEqual(path.stat().st_mode & 0o777,0o700)
    def test_recovery_refuses_any_previously_created_session_database(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            paths={name:root/name for name in ('STATE','CODE','SITE','AUTH')}
            for path in paths.values():path.mkdir()
            (paths['AUTH']/'sessions.db').touch()
            account=SimpleNamespace(pw_uid=os.getuid(),pw_dir='/nonexistent',pw_shell='/usr/sbin/nologin')
            with patch.multiple(promotion,**paths),patch.object(promotion.pwd,'getpwnam',return_value=account),patch.object(Path,'rename') as rename:
                with self.assertRaisesRegex(RuntimeError,'reviewed recovery point'):
                    promotion.archive_incomplete_preparation()
                rename.assert_not_called()

    def test_health_waits_for_scheduler_instead_of_accepting_early_ok(self):
        def response(value):
            item=Mock();item.__enter__=Mock(return_value=item);item.__exit__=Mock(return_value=False)
            item.read=Mock(return_value=json.dumps(value).encode());return item
        with patch.object(promotion,'urlopen',side_effect=[response({'ok':True,'scheduler_ok':False}),response({'ok':True,'scheduler_ok':True})]) as opened,patch.object(promotion.time,'sleep'):
            self.assertTrue(promotion.health('http://127.0.0.1:5055/health',require_scheduler=True)['scheduler_ok'])
            self.assertEqual(opened.call_count,2)
    def test_code_rollback_does_not_restore_databases_profiles_or_awg_configuration(self):
        plan=json.dumps(dict(old_enabled={unit:'enabled' for unit in promotion.OLD},caddy_mode=0o644))
        with patch.object(promotion,'run') as run,patch.object(promotion,'write') as write,patch.object(Path,'read_text',return_value=plan),patch.object(Path,'read_bytes',return_value=b'old caddy'),patch.object(Path,'unlink') as unlink,patch('builtins.print'):
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
    def test_live_origin_adds_only_8444_even_if_caddy_renumbers_servers(self):
        import route_live
        old={'apps':{'http':{'servers':{'srv0':{'listen':[':8443']},'srv1':{'listen':[':80']}}}}}
        new={'apps':{'http':{'servers':{'srv0':{'listen':[':8444']},'srv1':{'listen':[':8443']},'srv2':{'listen':[':80']}}}}}
        route_live.verify_listeners(old,new)
        new['apps']['http']['servers']['srv0']['listen']=[':443']
        with self.assertRaises(RuntimeError):route_live.verify_listeners(old,new)
    def test_production_and_preview_origins_are_distinct(self):
        from route_live import ROUTES
        self.assertIn('https://185-23-239-142.sslip.io:8444',ROUTES)
        self.assertNotIn(':443 ',ROUTES)
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
    def test_staging_credentials_tokens_and_origin_do_not_authorize_production(self):
        with tempfile.TemporaryDirectory() as folder:
            apps=[]
            for mode,code,port in [('stage','stage-test-code',8443),('live','live-test-code',8444)]:
                db=Path(folder)/(mode+'.db');sqlite3.connect(db).close()
                app=create_app(dict(TESTING=True,DB_PATH=str(db),ADMIN_CODE=code,ORIGIN='https://example.test:'+str(port)))
                apps.append(app.test_client())
            stage,live=apps
            stage_token=stage.post('/auth/login',json={'code':'stage-test-code'}).json['access_token']
            self.assertEqual(live.post('/auth/logout',headers={'Authorization':'Bearer '+stage_token}).status_code,401)
            self.assertEqual(live.post('/auth/login',json={'code':'stage-test-code'},headers={'Origin':'https://example.test:8444'}).status_code,401)
            self.assertEqual(live.post('/auth/login',json={'code':'live-test-code'},headers={'Origin':'https://example.test:8443'}).status_code,403)
            self.assertEqual(live.post('/auth/login',json={'code':'live-test-code'},headers={'Origin':'https://example.test:8444'}).status_code,200)
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
