"""Ensure the guarded Caddy change keeps the working WS and test2 routes."""

import importlib.util
from pathlib import Path
import unittest

module_path = Path(__file__).resolve().parents[1] / "deploy" / "hostkey-caddy-routes.py"
spec = importlib.util.spec_from_file_location("hostkey_caddy_routes", module_path)
routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routes)

LIVE = """193.107.211.46.sslip.io, vpn.caesarbot.ru {
    handle_path /test2/* {
        root * /var/www/caesar-test2
        file_server
    }

    @test2 path /sub/test-2.txt
    handle @test2 {
        header Content-Type "text/plain; charset=utf-8"
        root * /var/www/caesar-sub
        rewrite * /test-2.txt
        file_server
    }

    encode gzip
    handle /vless-ws {
        reverse_proxy 127.0.0.1:10000
    }
    handle {
        respond "CAESAR VPN HOSTKEY OK" 200
    }
}
"""


class CaddyRoutesTests(unittest.TestCase):
    def test_insertion_preserves_old_routes(self):
        revised, changed = routes.plan(LIVE)
        self.assertTrue(changed)
        self.assertEqual(revised.count("reverse_proxy 127.0.0.1:10000"), 1)
        self.assertEqual(revised.count("@test2 path /sub/test-2.txt"), 1)
        self.assertEqual(revised.count("reverse_proxy 127.0.0.1:5058"), 2)
        self.assertIn("^/sub/[A-Za-z0-9_-]{32,64}$", revised)
        self.assertIn('respond "CAESAR VPN HOSTKEY OK" 200', revised)

    def test_idempotency(self):
        first, _ = routes.plan(LIVE)
        again, changed = routes.plan(first)
        self.assertFalse(changed)
        self.assertEqual(first, again)

    def test_unrecognized_server_rejected(self):
        with self.assertRaises(ValueError):
            routes.plan(LIVE.replace("193.107.211.46.sslip.io", "example.com"))


if __name__ == "__main__":
    unittest.main()
