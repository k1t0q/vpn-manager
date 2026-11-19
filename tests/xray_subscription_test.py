from datetime import date, timedelta, datetime
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import xray_subscription
from xray_hostkey import provision, MOSCOW


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "clients.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE clients(id INTEGER PRIMARY KEY, period TEXT, start_date TEXT, end_date TEXT)")
            con.executemany("INSERT INTO clients VALUES(?,?,?,?)", [(1, "lifetime", "2026-01-01", None), (2, "1m", "2019-12-01", "2020-01-01")])
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
        self.assertIn("Ваши устройства".encode(), result["body"])
        self.assertIn("Ваш номер клиента: <strong>№1</strong>".encode(), result["body"])
        self.assertNotIn("Ваше персональное подключение".encode(), result["body"])
        self.assertIn("2 из 2 доступны".encode(), result["body"])
        self.assertIn(b"https://apps.apple.com/ru/app/uxway/id6762559624", result["body"])
        self.assertIn(b"https://github.com/INCY-DEV/incy-platforms/releases/download/latest/incy-windows-setup.exe", result["body"])
        self.assertIn("Подписка: Навсегда".encode(), result["body"])
        self.assertNotIn(b"role=\"progressbar\"", result["body"])
        self.assertEqual(result["headers"]["Referrer-Policy"], "no-referrer")
        self.assertNotIn(b"original-test", result["body"])

    def test_mobile_landing_is_top_aligned_without_affecting_desktop(self):
        page = self.request("/connect/" + self.landing_token)["body"].decode("utf-8")
        self.assertIn("@media (max-width:600px)", page)
        self.assertIn("body{display:block;min-height:100svh;padding:28px 18px 36px}", page)
        self.assertIn("main{margin:0 auto}", page)
        self.assertIn("body{margin:0;min-height:100vh;display:grid;place-items:center", page)
        self.assertIn('.foot span{display:block', page)
        self.assertIn('<span>У каждого устройства свой ключ.</span>', page)

    def test_remaining_days_and_progress_have_inclusive_deadline(self):
        current = date(2026, 11, 19)
        status = xray_subscription.subscription_display("1m", "2026-11-01", "2026-11-30", today=current)
        self.assertEqual(status["days"], 12)
        self.assertEqual(status["progress"], 40)
        self.assertEqual(status["end"], date(2026, 11, 30))
        self.assertEqual(xray_subscription.subscription_display("1m", "2026-11-01", "2026-11-19", today=current)["days"], 1)
        expired = xray_subscription.subscription_display("1m", "2026-11-01", "2026-11-18", today=current)
        self.assertEqual((expired["days"], expired["progress"]), (0, 0))

    def test_invalid_date_and_lifetime_do_not_invent_progress(self):
        lifetime = xray_subscription.subscription_display("lifetime", None, "2020-01-01", today=date(2026, 11, 19))
        self.assertTrue(lifetime["lifetime"])
        self.assertIsNone(lifetime["days"])
        unknown = xray_subscription.subscription_display("1m", "garbage", "nonsense", today=date(2026, 11, 19))
        self.assertIsNone(unknown["progress"])

    def test_renewal_in_database_updates_portal_without_changing_link(self):
        today = datetime.now(MOSCOW).date()
        first_end = today + timedelta(days=9)
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE clients SET period='1m',start_date=?,end_date=? WHERE id=1", (today.isoformat(), first_end.isoformat()))
        first = self.request("/connect/" + self.landing_token)["body"]
        self.assertIn("10 дней осталось".encode(), first)
        self.assertIn(b"role=\"progressbar\"", first)
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE clients SET end_date=? WHERE id=1", ((first_end + timedelta(days=14)).isoformat(),))
        updated = self.request("/connect/" + self.landing_token)["body"]
        self.assertIn("24 дня осталось".encode(), updated)
        self.assertNotIn("10 дней осталось".encode(), updated)

    def test_each_device_reports_own_activation_status(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE caesar_vless_devices SET manually_enabled=0 WHERE client_id=1 AND kind='pc'")
        page = self.request("/connect/" + self.landing_token)["body"]
        self.assertIn("Отключён".encode(), page)
        self.assertIn("1 из 2 доступны".encode(), page)
        self.assertIn(b"uxway://add/https://", page)
        self.assertNotIn(b"incy://import/https://", page)


if __name__ == "__main__":
    unittest.main()
