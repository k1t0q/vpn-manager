"""The trusted proxy change must preserve every unrelated Xray key/field."""

import copy
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/hostkey-risk-real-ip.py'
SPEC = importlib.util.spec_from_file_location('hostkey_risk_real_ip', SCRIPT)
realip = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(realip)


class RealIpTests(unittest.TestCase):
    def setUp(self):
        self.current = {'log': {'loglevel': 'warning'},
                        'inbounds': [{'tag': 'vless-ws', 'protocol': 'vless',
                                     'listen': '127.0.0.1', 'port': 10000,
                                     'streamSettings': {'network': 'ws', 'security': 'none',
                                                        'wsSettings': {'path': '/vless-ws'},
                                                        'sockopt': {'tcpKeepAliveIdle': 90}},
                                     'settings': {'clients': [
                                         {'id': 'test-secret-key-1', 'email': 'caesar-42-phone'},
                                         {'id': 'test-secret-key-2', 'email': 'caesar-42-pc'}]}},
                                    {'tag': 'other', 'protocol': 'socks', 'port': 1080}],
                        'outbounds': [{'protocol': 'freedom'}]}
        self.caddy = '''vpn.caesarbot.ru {
    handle /vless-ws {
        reverse_proxy 127.0.0.1:10000
    }
    handle /manager/* {
        reverse_proxy 127.0.0.1:5059
    }
}'''

    def test_single_field_change_preserves_devices_and_unmanaged_inbounds(self):
        old = copy.deepcopy(self.current)
        realip.verify_caddy(self.caddy)
        new, changed = realip.proposed_config(self.current)
        self.assertTrue(changed)
        self.assertEqual(self.current, old)
        self.assertEqual(new['inbounds'][0]['streamSettings']['sockopt'], {
            'tcpKeepAliveIdle': 90, 'trustedXForwardedFor': ['X-Forwarded-Proto']})
        del new['inbounds'][0]['streamSettings']['sockopt']['trustedXForwardedFor']
        self.assertEqual(new, old)

    def test_same_config_is_idempotent(self):
        first, _ = realip.proposed_config(self.current)
        second, changed = realip.proposed_config(first)
        self.assertFalse(changed)
        self.assertEqual(first, second)

    def test_never_overwrite_existing_different_trust_rules(self):
        self.current['inbounds'][0]['streamSettings']['sockopt']['trustedXForwardedFor'] = ['X-Private-Proxy']
        with self.assertRaises(realip.PreflightError):
            realip.proposed_config(self.current)

    def test_caddy_requires_exclusive_local_proxy_and_no_header_overrides(self):
        with self.assertRaises(realip.PreflightError):
            realip.verify_caddy(self.caddy.replace('127.0.0.1:10000', '0.0.0.0:10000'))
        with self.assertRaises(realip.PreflightError):
            realip.verify_caddy(self.caddy.replace('reverse_proxy 127.0.0.1:10000',
                'reverse_proxy 127.0.0.1:10000 {\n header_up X-Forwarded-For 127.0.0.1\n }'))
        with self.assertRaises(realip.PreflightError):
            realip.verify_caddy(self.caddy + self.caddy)


if __name__ == '__main__':
    unittest.main()
