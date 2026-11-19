"""Guarded, minimal Caddy insertion; dry-run by default.

Existing test2 and /vless-ws handlers are not modified or reordered.
For the pilot only the existing vpn.caesarbot.ru hostname is used; moving
connect.caesarbot.ru DNS must happen later with the final panel cutover.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


CADDY = Path("/etc/caddy/Caddyfile")
BACKUPS = Path("/root/caesar-caddy-backups")
START = "    # CAESAR_VLESS_ROUTES_BEGIN\n"
MARKER_END = "    # CAESAR_VLESS_ROUTES_END\n"
ROUTES = (
    START
    + '    @caesarProfiles path_regexp "^/sub/[A-Za-z0-9_-]{32,64}$"\n'
    + '    handle @caesarProfiles {\n'
    + '        reverse_proxy 127.0.0.1:5058\n'
    + '    }\n'
    + '    handle /connect/* {\n'
    + '        reverse_proxy 127.0.0.1:5058\n'
    + '    }\n'
    + MARKER_END
)


def plan(original: str) -> tuple[str, bool]:
    if START in original:
        if original.count(START) != 1 or original.count(MARKER_END) != 1 or ROUTES not in original:
            raise ValueError("Unexpected pre-existing CAESAR routes; review before editing")
        return original, False
    expected_host = "193.107.211.46.sslip.io, vpn.caesarbot.ru {\n"
    if original.count(expected_host) != 1:
        raise ValueError("Unexpected virtual host; manual review required")
    if "handle /vless-ws {" not in original or "reverse_proxy 127.0.0.1:10000" not in original:
        raise ValueError("Existing VLESS route was not found")
    if "@test2 path /sub/test-2.txt" not in original or "handle @test2" not in original:
        raise ValueError("Existing test subscription route was not found")
    return original.replace(expected_host, expected_host + ROUTES + "\n", 1), True


def run(command: list[str]):
    result = subprocess.run(command, text=True, capture_output=True, timeout=40, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Command failed")[-1000:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Atomically update Caddyfile and reload Caddy")
    opts = parser.parse_args()
    if opts.apply and os.geteuid() != 0:
        parser.error("--apply requires root")
    original = CADDY.read_text()
    updated, changed = plan(original)
    if not changed:
        print("CADDY_ROUTES_ALREADY_PRESENT")
        return
    fd, temporary = tempfile.mkstemp(prefix=".caesar-caddy-", suffix=".conf", dir=CADDY.parent)
    candidate = Path(temporary)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(updated)
        run(["/usr/bin/caddy", "validate", "--config", str(candidate), "--adapter", "caddyfile"])
        if not opts.apply:
            print("CADDY_ROUTES_VALIDATED: no live changes made")
            return
        # Preserve a private rollback before replacing the live Caddyfile.
        BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(BACKUPS, 0o700)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = BACKUPS / f"Caddyfile-before-{timestamp}"
        backup.write_text(original)
        os.chmod(backup, 0o600)
        before_stat = CADDY.stat()
        os.chown(candidate, before_stat.st_uid, before_stat.st_gid)
        os.chmod(candidate, stat.S_IMODE(before_stat.st_mode))
        if CADDY.read_text() != original:
            raise RuntimeError("Caddyfile changed during validation; refusing to overwrite")
        os.replace(candidate, CADDY)
        candidate = None
        try:
            run(["systemctl", "reload", "caddy"])
            run(["systemctl", "is-active", "--quiet", "caddy"])
        except Exception:
            restore = CADDY.with_name(".Caddyfile.caesar-rollback")
            restore.write_text(original)
            os.chown(restore, before_stat.st_uid, before_stat.st_gid)
            os.chmod(restore, stat.S_IMODE(before_stat.st_mode))
            os.replace(restore, CADDY)
            run(["systemctl", "reload", "caddy"])
            raise
        print(f"CADDY_ROUTES_APPLIED: backup={backup}")
    finally:
        if candidate is not None:
            candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        sys.exit(f"ERROR: {exc}")
