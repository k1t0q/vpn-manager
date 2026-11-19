import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from server.hostkey_panel import Panel, client_rows, panel_html


class HostkeyPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        self.db = path / 'clients.db'
        self.code = path / 'code'
        self.code.write_text('test-only-long-code-0123456789abcdefghij\n')
        with sqlite3.connect(self.db) as con:
            con.executescript('''
                CREATE TABLE clients(id INTEGER PRIMARY KEY, name TEXT, phone TEXT,
                    start_date TEXT, end_date TEXT, period TEXT, note TEXT,
                    created_at TEXT, referrer_id INTEGER, wg_client_id TEXT);
                CREATE TABLE client_history(id INTEGER PRIMARY KEY, client_id INTEGER,
                    event_type TEXT, description TEXT, created_at TEXT);
                CREATE TABLE caesar_vless_devices(client_id INTEGER, kind TEXT, uuid TEXT,
                    token TEXT, manually_enabled INTEGER, deployed INTEGER);
                CREATE TABLE caesar_connect_links(client_id INTEGER, token TEXT);
                INSERT INTO clients VALUES(33,'me','+000','2026-11-01',NULL,'lifetime',
                    'admin',NULL,NULL,NULL);
                INSERT INTO clients VALUES(34,'anna',NULL,'2026-11-01','2026-12-01','1m',
                    '',NULL,NULL,NULL);
                INSERT INTO caesar_vless_devices VALUES(33,'phone','secret-uuid-phone',
                    'secret-token-phone',1,1);
                INSERT INTO caesar_vless_devices VALUES(33,'pc','secret-uuid-pc',
                    'secret-token-pc',1,0);
                INSERT INTO caesar_connect_links VALUES(33,'secret-connect-token');
                INSERT INTO client_history VALUES(1,33,'created','Imported', '2026-11-19');
            ''')
        self.panel = Panel(self.db, self.code)

    def request(self, path, method='GET', body=None, token=None, origin='https://vpn.caesarbot.ru'):
        encoded = json.dumps(body).encode() if body is not None else b''
        environ = {
            'REQUEST_METHOD': method, 'PATH_INFO': '/manager-test' + path,
            'CONTENT_LENGTH': str(len(encoded)), 'wsgi.input': io.BytesIO(encoded),
            'REMOTE_ADDR': '192.0.2.4', 'HTTP_ORIGIN': origin,
            'HTTP_AUTHORIZATION': f'Bearer {token}' if token else '',
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
        self.assertIn('/manager-test/panel.js', data)
        self.assertIn('/manager-test/styles.css', data)
        self.assertNotIn('sqlite-manager.js', data)
        self.assertNotIn('<script src="config.js', data)
        self.assertIn('Тестовая панель HOSTKEY', data)


if __name__ == '__main__':
    unittest.main()
