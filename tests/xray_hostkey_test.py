"""Non-network, non-root regression checks for the HOSTKEY VLESS pilot."""

from datetime import date
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from xray_hostkey import active_subscription, check_database, provision, proposed_config, records, sync, MigrationError


SAMPLE = {
    "log": {"loglevel": "warning"},
    "inbounds": [{
        "tag": "vless-ws", "listen": "127.0.0.1", "port": 10000,
        "protocol": "vless", "settings": {"decryption": "none", "clients": [
            {"id": "original-test-one", "email": "test-1"},
            {"id": "original-test-two", "email": "test-2"},
        ]},
        "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/vless-ws"}}
    }],
    "outbounds": [{"protocol": "freedom"}],
}


class XrayMigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Path(self.dir.name) / "clients.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, period TEXT, end_date TEXT)")
            con.executemany("INSERT INTO clients VALUES(?,?,?)", [
                (1, "1m", "2026-12-31"),
                (2, "lifetime", None),
                (3, "1m", "2026-11-17"),
                (4, "imported", None),
            ])

    def tearDown(self):
        self.dir.cleanup()

    def test_subscription_date_boundary(self):
        self.assertTrue(active_subscription("1m", "2026-11-19", date(2026, 11, 19)))
        self.assertFalse(active_subscription("1m", "2026-11-18", date(2026, 11, 19)))
        self.assertTrue(active_subscription("lifetime", None, date(2026, 11, 19)))
        self.assertFalse(active_subscription("imported", None, date(2026, 11, 19)))

    def test_provision_idempotent_and_no_legacy_rewrites(self):
        provision(self.db, [1, 2])
        first = {(r["client_id"], r["kind"]): r["uuid"] for r in records(self.db)}
        provision(self.db, [2, 1])
        second = {(r["client_id"], r["kind"]): r["uuid"] for r in records(self.db)}
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertEqual(check_database(self.db), (True, 4))

    def test_bad_client_rolls_back_whole_transaction(self):
        with self.assertRaises(MigrationError):
            provision(self.db, [1, 999])
        self.assertEqual(records(self.db), [])

    def test_preserves_unmanaged_test_keys_on_reconcile(self):
        provision(self.db, [1, 2, 3, 4])
        planned, info = proposed_config(SAMPLE, records(self.db), date(2026, 11, 19))
        users = planned["inbounds"][0]["settings"]["clients"]
        self.assertEqual(users[:2], SAMPLE["inbounds"][0]["settings"]["clients"])
        self.assertEqual(len(users), 6)
        self.assertEqual(info["enabled"], 4)
        self.assertEqual(info["unmanaged_preserved"], 2)
        self.assertEqual(SAMPLE["inbounds"][0]["settings"]["clients"][0]["id"], "original-test-one")

    def test_expired_clients_are_removed_from_xray_while_lifetime_stays(self):
        provision(self.db, [1, 2, 3])
        older, _ = proposed_config(SAMPLE, records(self.db), date(2026, 11, 16))
        younger, _ = proposed_config(older, records(self.db), date(2026, 11, 19))
        ids = {row['id'] for row in younger['inbounds'][0]['settings']['clients']}
        self.assertEqual(len(older['inbounds'][0]['settings']['clients']) - len(ids), 2)
        self.assertIn('original-test-one', ids)
        self.assertIn('original-test-two', ids)
        devices = records(self.db)
        self.assertTrue(all(row['uuid'] not in ids for row in devices if row['client_id'] == 3))
        self.assertTrue(all(row['uuid'] in ids for row in devices if row['client_id'] == 2))

    def test_wrong_inbound_aborts(self):
        with self.assertRaises(MigrationError):
            proposed_config({"inbounds": []}, [])

    def test_caddy_terminated_tls_may_omit_xray_security(self):
        import json
        config = json.loads(json.dumps(SAMPLE))
        config["inbounds"][0]["streamSettings"].pop("security")
        planned, info = proposed_config(config, [])
        self.assertEqual(planned, config)
        self.assertEqual(info["unmanaged_preserved"], 2)

    def test_non_ws_security_is_still_rejected(self):
        import json
        config = json.loads(json.dumps(SAMPLE))
        config["inbounds"][0]["streamSettings"]["security"] = "tls"
        with self.assertRaises(MigrationError):
            proposed_config(config, [])

    def test_sync_without_apply_does_not_replace_config(self):
        import json
        self.config = Path(self.dir.name) / "config.json"
        self.config.write_text(json.dumps(SAMPLE))
        initial = self.config.read_bytes()
        provision(self.db, [1])
        from subprocess import CompletedProcess
        with patch("xray_hostkey._run", return_value=CompletedProcess([], 0, "Configuration OK", "")):
            result = sync(self.db, self.config, Path(self.dir.name) / "backups")
        self.assertTrue(result["validated"])
        self.assertFalse(result["applied"])
        self.assertEqual(self.config.read_bytes(), initial)

    def test_failed_xray_restart_restores_original_config(self):
        import json
        from subprocess import CompletedProcess
        config = Path(self.dir.name) / "config.json"
        config.write_text(json.dumps(SAMPLE))
        initial = config.read_bytes()
        provision(self.db, [1])
        backups = Path(self.dir.name) / "backups"
        commands = [
            CompletedProcess([], 0, "Configuration OK", ""),
            CompletedProcess([], 1, "", "Failed to restart"),
            CompletedProcess([], 1, "", "inactive"),
            CompletedProcess([], 0, "", ""),
        ]
        with patch("xray_hostkey._run", side_effect=commands), patch("xray_hostkey.os.geteuid", return_value=0):
            with self.assertRaises(MigrationError):
                sync(self.db, config, backups, apply=True)
        self.assertEqual(config.read_bytes(), initial)
        self.assertEqual(len(list(backups.glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
