import importlib.util
from pathlib import Path
import unittest


source = Path(__file__).resolve().parents[1] / 'deploy/hostkey-panel-production-caddy.py'
spec = importlib.util.spec_from_file_location('hostkey_panel_production_caddy', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProductionCaddyTests(unittest.TestCase):
    def setUp(self):
        self.old = '''193.107.211.46.sslip.io, vpn.caesarbot.ru {
    # CAESAR_VLESS_ROUTES_BEGIN
    handle /connect/* { reverse_proxy 127.0.0.1:5058 }
    # CAESAR_VLESS_ROUTES_END
    # CAESAR_MANAGER_PILOT_BEGIN
    handle /manager-test/* {
        reverse_proxy 127.0.0.1:5059
    }
    # CAESAR_MANAGER_PILOT_END
    handle /vless-ws { reverse_proxy 127.0.0.1:10000 }
    handle { respond "CAESAR VPN HOSTKEY OK" 200 }
}
'''

    def test_switches_to_manager_preserving_all_other_routes(self):
        result, changed = module.plan(self.old)
        self.assertTrue(changed)
        self.assertIn('handle /manager/*', result)
        self.assertIn('handle /manager-test/*', result)
        self.assertIn('reverse_proxy 127.0.0.1:10000', result)
        self.assertIn('handle /connect/*', result)
        self.assertNotIn('CAESAR_MANAGER_PILOT_BEGIN', result)
        self.assertEqual(module.plan(result), (result, False))

    def test_refuses_unexpected_route(self):
        with self.assertRaises(ValueError):
            module.plan(self.old.replace('reverse_proxy 127.0.0.1:5059', 'respond "wrong" 200'))


if __name__ == '__main__':
    unittest.main()
