"""Conservative CAESAR VLESS-over-WebSocket migration controller.

The AdminVPS database is copied *first* to a separate working database.
This module never changes the legacy clients/history tables, existing Xray
users, Caddy or certificates. A live Xray restart requires explicit --apply.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid


MOSCOW = ZoneInfo("Europe/Moscow")
KINDS = ("phone", "pc")
DEFAULT_DB = Path("/var/lib/caesar-manager/clients.db")
DEFAULT_XRAY = Path("/usr/local/etc/xray/config.json")
DEFAULT_BACKUP = Path("/root/caesar-xray-backups")
DEFAULT_LOCK = Path("/run/lock/caesar-xray-manager.lock")


class MigrationError(Exception):
    pass


def active_subscription(period: str | None, end_date: str | None, today: date | None = None) -> bool:
    """On the paid-through date the client remains active until Moscow midnight.

    Unknown/no-end legacy records are held for manual review, not assumed paid.
    """
    today = today or datetime.now(MOSCOW).date()
    if period == "lifetime":
        return True
    if not end_date:
        return False
    try:
        return date.fromisoformat(end_date[:10]) >= today
    except ValueError:
        return False


def init_tables(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS caesar_vless_devices (
          client_id INTEGER NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('phone', 'pc')),
          uuid TEXT NOT NULL UNIQUE,
          token TEXT NOT NULL UNIQUE,
          manually_enabled INTEGER NOT NULL DEFAULT 1 CHECK(manually_enabled IN (0, 1)),
          deployed INTEGER NOT NULL DEFAULT 0 CHECK(deployed IN (0, 1)),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(client_id, kind),
          FOREIGN KEY(client_id) REFERENCES clients(id)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS caesar_connect_links (
          client_id INTEGER PRIMARY KEY,
          token TEXT NOT NULL UNIQUE,
          FOREIGN KEY(client_id) REFERENCES clients(id)
        )
    """)


def check_database(db_path: Path) -> tuple[bool, int]:
    if not db_path.is_file():
        raise MigrationError(f"Database missing: {db_path}")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        count = con.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        return ok, count


def provision(db_path: Path, ids: list[int]) -> list[int]:
    """Register device UUIDs without touching Xray. Idempotent for each ID."""
    if not ids or any(type(cid) is not int or cid <= 0 for cid in ids):
        raise MigrationError("Provide explicit positive client IDs")
    with sqlite3.connect(db_path, timeout=30) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        init_tables(con)
        result = []
        for cid in sorted(set(ids)):
            client = con.execute("SELECT id FROM clients WHERE id=?", (cid,)).fetchone()
            if client is None:
                raise MigrationError(f"No client #{cid}; no changes committed")
            con.execute(
                "INSERT OR IGNORE INTO caesar_connect_links(client_id,token) VALUES(?,?)",
                (cid, secrets.token_urlsafe(32)),
            )
            for kind in KINDS:
                con.execute(
                    "INSERT OR IGNORE INTO caesar_vless_devices(client_id,kind,uuid,token) VALUES(?,?,?,?)",
                    (cid, kind, str(uuid.uuid4()), secrets.token_urlsafe(32)),
                )
            result.append(cid)
        return result


def records(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        found = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='caesar_vless_devices'").fetchone()
        if not found:
            return []
        return con.execute("""
            SELECT d.client_id, d.kind, d.uuid, d.manually_enabled,
                   c.period, c.end_date
              FROM caesar_vless_devices d
              JOIN clients c ON c.id=d.client_id
             ORDER BY d.client_id, d.kind
        """).fetchall()


def portal_links(db_path: Path, ids: list[int]) -> dict[int, str]:
    if not ids:
        raise MigrationError("Provide explicit client IDs for links")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        found = dict(con.execute("SELECT client_id, token FROM caesar_connect_links"))
    missing = [cid for cid in ids if cid not in found]
    if missing:
        raise MigrationError("Client has no VLESS profile yet: " + ", ".join(map(str, missing)))
    return {cid: "https://connect.caesarbot.ru/" + found[cid] for cid in ids}


def find_inbound(config: dict, tag: str = "vless-ws") -> dict:
    candidates = [i for i in config.get("inbounds", []) if i.get("tag") == tag]
    if len(candidates) != 1:
        raise MigrationError(f"Expected exactly one inbound with tag {tag!r}")
    inbound = candidates[0]
    stream = inbound.get("streamSettings", {})
    if (inbound.get("protocol"), inbound.get("listen"), inbound.get("port")) != (
        "vless", "127.0.0.1", 10000
    ):
        raise MigrationError("Unexpected VLESS inbound; refusing production changes")
    if (stream.get("network"), stream.get("security")) != ("ws", "none"):
        raise MigrationError("Unexpected transport/security; refusing production changes")
    clients = inbound.get("settings", {}).get("clients")
    if not isinstance(clients, list):
        raise MigrationError("Inbound client list is missing")
    return inbound


def proposed_config(config: dict, device_rows: list[sqlite3.Row], today: date | None = None) -> tuple[dict, dict]:
    """Preserve all unmanaged users; reconcile only UUIDs we have issued."""
    proposed = json.loads(json.dumps(config))
    inbound = find_inbound(proposed)
    current = inbound["settings"]["clients"]
    ids = [entry.get("id") for entry in current]
    if len(set(ids)) != len(ids) or any(not isinstance(x, str) for x in ids):
        raise MigrationError("Current Xray client IDs are missing or duplicated")
    ours = {row["uuid"] for row in device_rows}
    preserved = [entry for entry in current if entry["id"] not in ours]
    if any(not isinstance(entry, dict) for entry in preserved):
        raise MigrationError("Malformed existing Xray client")
    enabled = [row for row in device_rows if row["manually_enabled"] and active_subscription(row["period"], row["end_date"], today)]
    current_ids = {entry["id"] for entry in preserved}
    if current_ids & ours:
        raise MigrationError("Existing unmanaged UUID conflicts with managed UUID")
    for row in enabled:
        preserved.append({"id": row["uuid"], "email": f"caesar-{row['client_id']}-{row['kind']}"})
    inbound["settings"]["clients"] = preserved
    return proposed, {"managed": len(device_rows), "enabled": len(enabled), "unmanaged_preserved": len(current_ids)}


@contextmanager
def locked(path: Path = DEFAULT_LOCK):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationError(f"Command could not complete: {args[0]}") from exc


def _private_copy(src: Path, dst: Path) -> None:
    """Copy config preserving owner and mode; never follow a surprise symlink."""
    original = src.stat(follow_symlinks=False)
    if not stat.S_ISREG(original.st_mode):
        raise MigrationError(f"Not a regular file: {src}")
    shutil.copyfile(src, dst, follow_symlinks=False)
    os.chmod(dst, stat.S_IMODE(original.st_mode))
    os.chown(dst, original.st_uid, original.st_gid)


def sync(db_path: Path, xray_path: Path, backup_dir: Path, apply: bool = False) -> dict:
    if not check_database(db_path)[0]:
        raise MigrationError("Database is not healthy")
    with locked():
        baseline = xray_path.read_bytes()
        original = json.loads(baseline)
        new_config, info = proposed_config(original, records(db_path))
        candidate = json.dumps(new_config, indent=2, ensure_ascii=False).encode() + b"\n"
        if new_config == original:
            if apply:
                mark_deployed(db_path, new_config)
            return {**info, "change": False, "applied": False}
        # Validate the generated configuration before allowing production changes.
        temp_path = None
        try:
            fd, temp_name = tempfile.mkstemp(prefix="caesar-xray-", suffix=".json", dir=xray_path.parent)
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(candidate)
            check = _run(["/usr/local/bin/xray", "run", "-test", "-config", str(temp_path)])
            if check.returncode:
                raise MigrationError("Xray rejected proposed configuration: " + check.stderr[-1200:])
            if not apply:
                return {**info, "change": True, "validated": True, "applied": False}
            if os.geteuid() != 0:
                raise MigrationError("--apply requires root privileges")
            # Abort if another administrator edited the live config during validation.
            if hashlib.sha256(xray_path.read_bytes()).digest() != hashlib.sha256(baseline).digest():
                raise MigrationError("Xray file changed during validation; retry")
            backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(backup_dir, 0o700)
            timestamp = datetime.now(MOSCOW).strftime("%Y%m%d-%H%M%S-%f")
            backup = backup_dir / f"config-before-{timestamp}.json"
            _private_copy(xray_path, backup)
            os.chmod(backup, 0o600)
            _private_copy(xray_path, temp_path)
            temp_path.write_bytes(candidate)
            os.replace(temp_path, xray_path)
            temp_path = None
            try:
                restart = _run(["systemctl", "restart", "xray"], timeout=45)
                active = _run(["systemctl", "is-active", "--quiet", "xray"])
                if restart.returncode or active.returncode:
                    raise MigrationError("Restarted Xray did not become healthy")
            except MigrationError:
                restore = xray_path.with_name(xray_path.name + ".caesar-rollback")
                _private_copy(backup, restore)
                os.replace(restore, xray_path)
                _run(["systemctl", "restart", "xray"], timeout=45)
                raise
            mark_deployed(db_path, new_config)
            return {**info, "change": True, "applied": True, "backup": str(backup)}
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def mark_deployed(db_path: Path, new_config: dict) -> None:
    """The published state follows a successful Xray start, never a dry run."""
    enabled_uuids = {entry["id"] for entry in find_inbound(new_config)["settings"]["clients"]}
    with sqlite3.connect(db_path, timeout=30) as con:
        con.execute("BEGIN IMMEDIATE")
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='caesar_vless_devices'").fetchone()
        if not exists:
            return
        con.execute("UPDATE caesar_vless_devices SET deployed=0")
        con.executemany("UPDATE caesar_vless_devices SET deployed=1 WHERE uuid=?", [(item,) for item in enabled_uuids])


def main() -> None:
    parser = argparse.ArgumentParser(description="CAESAR HOSTKEY Xray pilot manager")
    parser.add_argument("command", choices=["inventory", "provision", "sync", "links"])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--xray", type=Path, default=DEFAULT_XRAY)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--ids", help="Explicit comma-separated billing client IDs")
    parser.add_argument("--apply", action="store_true", help="Explicitly restart Xray if config changed")
    args = parser.parse_args()
    try:
        healthy, count = check_database(args.db)
        if not healthy:
            raise MigrationError("Working database failed SQLite integrity check")
        if args.command == "inventory":
            print(json.dumps({"database_healthy": healthy, "clients": count, "registered_devices": len(records(args.db))}))
        elif args.command == "provision":
            if args.apply or not args.ids or not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", args.ids):
                raise MigrationError("Provision needs --ids 1,2 and never accepts --apply")
            print(json.dumps({"registered_client_ids": provision(args.db, [int(x) for x in args.ids.split(',')]), "xray_unchanged": True}))
        elif args.command == "links":
            if not args.ids or not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", args.ids):
                raise MigrationError("Links require --ids 1,2")
            print(json.dumps(portal_links(args.db, [int(x) for x in args.ids.split(',')]), indent=2))
        else:
            print(json.dumps(sync(args.db, args.xray, args.backup_dir, apply=args.apply)))
    except (MigrationError, sqlite3.Error, ValueError, OSError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
