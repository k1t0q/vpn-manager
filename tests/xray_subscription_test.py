from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import xray_subscription
from xray_hostkey import provision, active_subscription


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "clients.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE clients(id INTEGER PRIMARY KEY, period TEXT, end_date TEXT)")
            con.executemany("INSERT INTO clients VALUES(?,?,?)", [(1, "lifetime", None), (2, "1m", "2020-01-01")])
        provision(self.db, [1, 2])
        with sqlite3.connect(self.db) as con:
            self.live_token = con.execute("SELECT token FROM caesar_vless_devices WHERE client_id=1 AND kind='phone'").fetchone()[0]
            self.expired_token = con.execute("SELECT token FROM caesar_vless_devices WHERE client_id=2 AND kind='pc'").fetchone()[0]
            self.landing_token = con.execute("SELECT token FROM caesar_connect_links WHERE client_id=1").fetchone()[0]
            con.execute("UPDATE caesar_vless_devices SET deployed=1 WHERE client_id=1")

    def tearDown(self):
        self.temp.cleanup()

    def request(self, path, method="GET"):
        output = {}
        def start_response(status, headers):
            output["status"] = status
            output["headers"] = dict(headers)
        with patch.object(xray_subscription, "DB_PATH", self.db):
            output["body"] = b"".join(xray_subscription.app({"PATH_INFO": path, "REQUEST_METHOD": method}, start_response))
        return output

    def test_lifetime_subscription_has_no_expiry_and_correct_transport(self):
        result = self.request("/sub/" + self.live_token)
        self.assertEqual(result["status"], "200 OK")
        self.assertIn(b"security=tls", result["body"])
        self.assertIn(b"type=ws", result["body"])
        self.assertIn(b"path=%2Fvless-ws", result["body"])
        self.assertIn(b"#CAESAR%20VPN", result["body"])
        self.assertNotIn("expire=", result["headers"]["Subscription-Userinfo"])

    def test_inactive_profile_withholds_secret(self):
        result = self.request("/sub/" + self.expired_token)
        self.assertEqual(result["status"], "410 Gone")
        self.assertNotIn(b"vless://", result["body"])

    def test_unknown_token_does_not_disclose_database(self):
        result = self.request("/sub/" + "z" * 43)
        self.assertEqual(result["status"], "404 Not Found")

    def test_dry_run_profiles_are_not_served(self):
        with sqlite3.connect(self.db) as con:
            token = con.execute("SELECT token FROM caesar_vless_devices WHERE client_id=1 AND kind='pc'").fetchone()[0]
            con.execute("UPDATE caesar_vless_devices SET deployed=0 WHERE client_id=1 AND kind='pc'")
        self.assertEqual(self.request("/sub/" + token)["status"], "503 Service Unavailable")

    def test_head_has_headers_without_body(self):
        result = self.request("/sub/" + self.live_token, "HEAD")
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["body"], b"")
        self.assertIn("Subscription-Userinfo", result["headers"])

    def test_landing_has_distinct_one_click_links_for_two_devices(self):
        result = self.request("/connect/" + self.landing_token)
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["headers"]["Content-Type"], "text/html; charset=utf-8")
        self.assertIn(b"uxway://add/https://", result["body"])
        self.assertIn(b"incy://import/https://", result["body"])
        self.assertNotIn(b"original-test", result["body"])


if __name__ == "__main__":
    unittest.main()
