import importlib.util
from pathlib import Path
import unittest


source = Path(__file__).resolve().parents[1] / 'deploy/hostkey-panel-caddy.py'
spec = importlib.util.spec_from_file_location('hostkey_panel_caddy', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PanelCaddyTests(unittest.TestCase):
    def setUp(self):
        self.original = '''193.107.211.46.sslip.io, vpn.caesarbot.ru {
    # CAESAR_VLESS_ROUTES_BEGIN
    handle /connect/* { reverse_proxy 127.0.0.1:5058 }
    # CAESAR_VLESS_ROUTES_END
    @test2 path /sub/test-2.txt
    handle @test2 { respond "test" 200 }
    handle /vless-ws { reverse_proxy 127.0.0.1:10000 }
    handle { respond "CAESAR VPN HOSTKEY OK" 200 }
}
'''

    def test_additive_and_idempotent(self):
        result, changed = module.plan(self.original)
        self.assertTrue(changed)
        self.assertIn('handle /manager-test/*', result)
        self.assertIn('reverse_proxy 127.0.0.1:10000', result)
        self.assertIn('handle @test2', result)
        self.assertEqual(module.plan(result), (result, False))

    def test_unexpected_existing_route_fails_closed(self):
        with self.assertRaises(ValueError):
            module.plan(self.original.replace('    # CAESAR_VLESS_ROUTES_END',
                '    # CAESAR_VLESS_ROUTES_END\n    handle /manager-test/* { respond "bad" 200 }'))


if __name__ == '__main__':
    unittest.main()
