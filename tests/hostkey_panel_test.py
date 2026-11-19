import io
import base64
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from server.hostkey_panel import Panel, client_rows, panel_html
from server.hostkey_panel_ops import PanelOperations


class HostkeyPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        self.db = path / 'clients.db'
        self.code = path / 'code'
        self.push_db = path / 'push.db'
        self.push_key = path / 'push-public.key'
        self.push_key.write_text(base64.urlsafe_b64encode(b'\x04' + b'\x01' * 64).decode().rstrip('='))
        self.code.write_text('test-only-long-code-0123456789abcdefghij\n')
        with sqlite3.connect(self.db) as con:
            con.executescript('''
                CREATE TABLE clients(id INTEGER PRIMARY KEY, user_id TEXT, name TEXT, phone TEXT,
                    start_date TEXT, end_date TEXT, period TEXT, note TEXT,
                    created_at TEXT, referrer_id INTEGER, wg_client_id TEXT);
                CREATE TABLE client_history(id INTEGER PRIMARY KEY, user_id TEXT, client_id INTEGER,
                    event_type TEXT, description TEXT, created_at TEXT);
                CREATE TABLE caesar_vless_devices(client_id INTEGER, kind TEXT, uuid TEXT,
                    token TEXT, manually_enabled INTEGER, deployed INTEGER);
                CREATE TABLE caesar_connect_links(client_id INTEGER, token TEXT);
                INSERT INTO clients VALUES(33,'owner','me','+000','2026-11-01',NULL,'lifetime',
                    'admin',NULL,NULL,NULL);
                INSERT INTO clients VALUES(34,'owner','anna',NULL,'2026-11-01','2026-12-01','1m',
                    '',NULL,NULL,NULL);
                INSERT INTO caesar_vless_devices VALUES(33,'phone','secret-uuid-phone',
                    'secret-token-phone',1,1);
                INSERT INTO caesar_vless_devices VALUES(33,'pc','secret-uuid-pc',
                    'secret-token-pc',1,0);
                INSERT INTO caesar_connect_links VALUES(33,'secret-connect-token');
                INSERT INTO client_history VALUES(1,'owner',33,'created','Imported', '2026-11-19');
            ''')
        self.panel = Panel(self.db, self.code, push_db=self.push_db, push_key=self.push_key)

    def request(self, path, method='GET', body=None, token=None, origin='https://vpn.caesarbot.ru', request_id='test-operation-idempotency-123'):
        encoded = json.dumps(body).encode() if body is not None else b''
        path_only, _, query = path.partition('?')
        environ = {
            'REQUEST_METHOD': method, 'PATH_INFO': '/manager' + path_only, 'QUERY_STRING': query,
            'CONTENT_LENGTH': str(len(encoded)), 'wsgi.input': io.BytesIO(encoded),
            'REMOTE_ADDR': '192.0.2.4', 'HTTP_ORIGIN': origin,
            'HTTP_AUTHORIZATION': f'Bearer {token}' if token else '',
            'HTTP_IDEMPOTENCY_KEY': request_id,
        }
        sent = {}

        def start(status, headers):
            sent.update(status=status, headers=dict(headers))

        data = b''.join(self.panel(environ, start))
        value = json.loads(data) if 'application/json' in sent['headers']['Content-Type'] else data
        return sent['status'], value, sent['headers']

    def login(self):
        status, value, _ = self.request('/auth/login', method='POST',
            body={'code': self.code.read_text().strip()})
        self.assertEqual(status, '200 OK')
        return value['access_token']

    def test_auth_required_before_private_data(self):
        status, _, _ = self.request('/api/clients')
        self.assertEqual(status, '401 Unauthorized')
        status, _, _ = self.request('/api/clients/33/link')
        self.assertEqual(status, '401 Unauthorized')
        status, _, _ = self.request('/api/push/key')
        self.assertEqual(status, '401 Unauthorized')
        status, _, _ = self.request('/auth/login', method='POST', body={'code': 'wrong'}, origin='https://example.org')
        self.assertEqual(status, '403 Forbidden')

    def test_devices_and_billing_read_without_uuid_leaks(self):
        token = self.login()
        status, value, headers = self.request('/api/clients', token=token)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(value['clients']), 2)
        self.assertEqual(value['clients'][0]['name'], 'anna')
        self.assertEqual(value['clients'][1]['devices']['phone']['deployed'], True)
        self.assertEqual(value['clients'][1]['devices']['pc']['deployed'], False)
        self.assertTrue(value['clients'][1]['has_portal'])
        self.assertFalse(value['clients'][0]['has_portal'])
        self.assertEqual(value['clients'][1]['vpn_state'], 'pending')
        self.assertEqual(value['clients'][1]['vpn_deployed_keys'], 1)
        self.assertEqual(value['clients'][1]['vpn_target_keys'], 2)
        self.assertEqual(value['clients'][0]['vpn_state'], 'none')
        self.assertNotIn('secret-uuid', json.dumps(value))
        self.assertNotIn('secret-token', json.dumps(value))
        self.assertEqual(headers['Cache-Control'], 'no-store, private')

    def test_link_only_on_authorized_endpoint(self):
        token = self.login()
        status, value, _ = self.request('/api/clients/33/link', token=token)
        self.assertEqual(status, '200 OK')
        self.assertEqual(value['url'], 'https://vpn.caesarbot.ru/connect/secret-connect-token')
        status, _, _ = self.request('/api/clients/34/link', token=token)
        self.assertEqual(status, '404 Not Found')

    def test_no_write_operations_even_after_login(self):
        token = self.login()
        status, _, _ = self.request('/api/clients/33', 'POST', {'name': 'deleted'}, token)
        self.assertEqual(status, '405 Method Not Allowed')
        self.assertEqual(client_rows(self.db)[0]['name'], 'anna')
        status, value, _ = self.request('/api/clients/33/history', token=token)
        self.assertEqual(status, '200 OK')
        self.assertEqual(value['history'][0]['event_type'], 'created')

    def test_logout_invalidates_bearer(self):
        token = self.login()
        status, _, _ = self.request('/auth/logout', 'POST', {}, token)
        self.assertEqual(status, '200 OK')
        status, _, _ = self.request('/api/clients', token=token)
        self.assertEqual(status, '401 Unauthorized')

    def test_frontend_has_no_old_awg_scripts(self):
        data = panel_html().decode()
        self.assertIn('/manager/panel.js', data)
        self.assertIn('/manager/push.js', data)
        self.assertIn("worker-src 'self'", self.request('/')[2]['Content-Security-Policy'])
        self.assertIn('/manager/styles.css', data)
        self.assertNotIn('sqlite-manager.js', data)
        self.assertNotIn('<script src="config.js', data)
        self.assertIn('CAESAR VPN MANAGER: защищённое управление', data)
        self.assertIn('<th>Осталось дней</th><th>Доступ VPN</th><th>Действия</th>', data)
        self.assertNotIn('<th>Подписка</th>', data)
        self.assertEqual(data.split('<tbody id="rows">')[0].split('<thead><tr>')[-1].count('<th>'), 10)
        panel_js = (Path(__file__).resolve().parents[1] / 'server/hostkey_panel.js').read_text()
        self.assertIn('class="remaining-days"', panel_js)
        self.assertNotIn('class="subscription-cell"', panel_js)
        self.assertIn('.m-scroll.desktop th:last-child,.m-scroll.desktop td:last-child', data)
        self.assertIn('position:sticky;right:0', data)
        self.assertIn('.m-scroll.desktop .client-actions{display:grid;', data)
        self.assertIn('max-width:1920px', data)
        self.assertIn('.remaining-days{font-size:13px', data)
        self.assertIn('max-width:1650px', data)
        self.assertIn('max-width:1350px', data)
        self.assertNotIn('Пригласил: ${esc(findReferrer(c.referrer_id))}', panel_js)
        self.assertIn('pilot-devices-mobile', panel_js)
        self.assertIn('devices(c, true)', panel_js)
        self.assertIn('setInterval(refreshPendingStatuses, 10000)', panel_js)
        self.assertIn("state.clients.some(c => c.vpn_state === 'pending')", panel_js)
        self.assertIn('if (result.sync_pending) setTimeout(refreshPendingStatuses, 2500)', panel_js)
        self.assertIn('.cards .info{display:grid;grid-template-columns:minmax(0,1fr)', data)
        self.assertIn('.m-grid.layout .card .client-actions button{display:flex;align-items:center;justify-content:center;', data)
        self.assertNotIn('VPN MANAGER · HOSTKEY', data)

    def test_push_assets_are_scoped_to_manager_and_public(self):
        status, body, _ = self.request('/manifest.webmanifest')
        self.assertEqual(status, '200 OK')
        manifest = json.loads(body)
        self.assertEqual(manifest['scope'], '/manager/')
        self.assertEqual(manifest['start_url'], '/manager/')
        self.assertTrue(all(icon['src'].startswith('/manager/') for icon in manifest['icons']))
        for path in ('/push.js', '/sw.js', '/push-icon-192.png'):
            status, body, _ = self.request(path)
            self.assertEqual(status, '200 OK')
            self.assertTrue(body)

    def test_push_registration_and_test_queue_require_admin_session(self):
        token = self.login()
        status, key, _ = self.request('/api/push/key', token=token)
        self.assertEqual(status, '200 OK')
        self.assertEqual(key['public_key'], self.push_key.read_text())
        subscription = {'endpoint': 'https://fcm.googleapis.com/fcm/send/demo',
                        'keys': {'p256dh': key['public_key'],
                                 'auth': base64.urlsafe_b64encode(b'\x03' * 16).decode().rstrip('=')}}
        status, _, _ = self.request('/api/push/subscribe', 'POST', subscription)
        self.assertEqual(status, '401 Unauthorized')
        status, result, _ = self.request('/api/push/subscribe', 'POST', subscription, token)
        self.assertEqual(status, '200 OK')
        device = result['device_id']
        status, result, _ = self.request('/api/push/test', 'POST', {'device_id': device}, token)
        self.assertEqual(status, '200 OK')
        status, result, _ = self.request('/api/push/test-status?ticket=' + result['ticket'], token=token)
        self.assertEqual((status, result['state']), ('200 OK', 'pending'))
        status, _, _ = self.request('/api/push/unsubscribe', 'POST', {'device_id': device}, token)
        self.assertEqual(status, '200 OK')

    def test_vpn_status_never_confuses_paid_status_with_deployed_keys(self):
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE caesar_vless_devices SET deployed=1 WHERE client_id=33')
        self.assertEqual(client_rows(self.db)[1]['vpn_state'], 'on')
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE caesar_vless_devices SET manually_enabled=0 WHERE client_id=33 AND kind="phone"')
        self.assertEqual(client_rows(self.db)[1]['vpn_state'], 'pending')
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE caesar_vless_devices SET deployed=0 WHERE client_id=33 AND kind="phone"')
        self.assertEqual(client_rows(self.db)[1]['vpn_state'], 'partial')
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE clients SET end_date="2020-01-01",period="1m" WHERE id=33')
        self.assertEqual(client_rows(self.db)[1]['vpn_state'], 'pending')
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE caesar_vless_devices SET deployed=0 WHERE client_id=33')
        self.assertEqual(client_rows(self.db)[1]['vpn_state'], 'expired')

    def test_legacy_panel_url_redirects_to_production(self):
        sent = {}
        def start(status, headers):
            sent['status'] = status
            sent['headers'] = dict(headers)
        body = b''.join(self.panel({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/manager-test/'}, start))
        self.assertEqual(sent['status'], '308 Permanent Redirect')
        self.assertEqual(sent['headers']['Location'], '/manager/')
        self.assertEqual(body, b'')

    def test_login_is_hidden_until_existing_session_has_loaded(self):
        data = panel_html().decode()
        self.assertIn('id="pilotBoot"', data)
        self.assertIn('html:not(.caesar-panel-ready) #loginPane', data)
        self.assertIn('html.caesar-panel-ready #pilotBoot{display:none}', data)
        js = (Path(__file__).resolve().parents[1] / 'server/hostkey_panel.js').read_text()
        self.assertIn('if (state.token) loadClients().finally(finishBoot)', js)
        self.assertIn('else finishBoot()', js)

    def test_styles_are_allowed_by_csp(self):
        _, _, headers = self.request('/')
        self.assertIn("style-src 'self' 'unsafe-inline'", headers['Content-Security-Policy'])

    def test_write_endpoint_is_allowlisted_origin_bound_and_audited(self):
        self.panel.ops = PanelOperations(self.db, frozenset({33}), backup_dir=Path(self.temp.name)/'backups')
        token = self.login()
        path = '/api/clients/33/edit'
        status, _, _ = self.request(path,'POST',{'name':'pilot'},origin='https://evil.invalid',token=token)
        self.assertEqual(status,'403 Forbidden')
        status, _, _ = self.request('/api/clients/34/edit','POST',{'name':'blocked'},token=token)
        self.assertEqual(status,'403 Forbidden')
        status, value, _ = self.request(path,'POST',{'name':'pilot'},token=token)
        self.assertEqual(status,'200 OK')
        self.assertTrue(value['ok'])
        status, value, _ = self.request(path,'POST',{'name':'pilot'},token=token)
        self.assertEqual(status,'200 OK')  # Idempotent retry.
        status, value, _ = self.request('/api/clients',token=token)
        self.assertEqual(status,'200 OK')
        self.assertEqual(value['clients'][1]['name'],'pilot')
        self.assertTrue(value['clients'][1]['can_manage'])
        self.assertFalse(value['clients'][0]['can_manage'])
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM client_history WHERE event_type="edited"').fetchone()[0],1)

    def test_full_mode_allows_real_client_creation_and_management(self):
        with patch.dict(os.environ, {'CAESAR_PANEL_MODE': 'full', 'CAESAR_PANEL_PILOT_IDS': '93'}):
            self.panel.ops = PanelOperations.from_environment(self.db)
        token = self.login()
        status, data, _ = self.request('/api/clients', token=token)
        self.assertEqual(status, '200 OK')
        self.assertTrue(data['full_access'])
        self.assertTrue(all(c['can_manage'] for c in data['clients']))
        status, result, _ = self.request('/api/clients', 'POST', {
            'name': 'Обычный клиент', 'period': '1m', 'phone': '+000', 'start_date': '2026-11-19',
            'referrer_id': None}, token=token, request_id='full-mode-new-client-001')
        self.assertEqual(status, '201 Created')
        self.assertTrue(result['ok'])
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM caesar_vless_devices WHERE client_id=?',
                                         (result['id'],)).fetchone()[0], 2)
        self.assertTrue(list((self.db.parent / 'panel-backups').glob('*.db')))

    def test_unknown_panel_mode_fails_closed(self):
        with patch.dict(os.environ, {'CAESAR_PANEL_MODE': 'everything'}):
            with self.assertRaises(RuntimeError):
                PanelOperations.from_environment(self.db)


if __name__ == '__main__':
    unittest.main()
