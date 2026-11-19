"""Add only /manager-test/* route to the existing HOSTKEY Caddy site.

Dry-run is the default. Backups, validate, atomic install and rollback on apply.
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


CADDY = Path('/etc/caddy/Caddyfile')
BACKUPS = Path('/root/caesar-caddy-backups')
START = '    # CAESAR_VLESS_ROUTES_BEGIN\n'
END = '    # CAESAR_VLESS_ROUTES_END\n'
PANEL_START = '    # CAESAR_MANAGER_PILOT_BEGIN\n'
PANEL_END = '    # CAESAR_MANAGER_PILOT_END\n'
BLOCK = (PANEL_START +
         '    handle /manager-test/* {\n'
         '        reverse_proxy 127.0.0.1:5059\n'
         '    }\n' + PANEL_END)


def plan(original: str) -> tuple[str, bool]:
    if original.count(START) != 1 or original.count(END) != 1:
        raise ValueError('Missing or duplicated VLESS migration markers')
    if 'reverse_proxy 127.0.0.1:10000' not in original or 'handle /vless-ws {' not in original:
        raise ValueError('Existing Xray upstream missing')
    if '193.107.211.46.sslip.io, vpn.caesarbot.ru {' not in original:
        raise ValueError('Unexpected site block')
    if PANEL_START in original or PANEL_END in original:
        if original.count(PANEL_START) == 1 and original.count(PANEL_END) == 1 and BLOCK in original:
            return original, False
        raise ValueError('Existing panel route differs; review first')
    if 'handle /manager-test/*' in original or 'reverse_proxy 127.0.0.1:5059' in original:
        raise ValueError('Unmanaged panel proxy already exists')
    return original.replace(END, END + BLOCK + '\n', 1), True


def run(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'command failed')[-1200:])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and os.geteuid() != 0:
        parser.error('Root required to apply')
    original = CADDY.read_text()
    updated, changed = plan(original)
    if not changed:
        print('PANEL_CADDY_ALREADY_PRESENT')
        return
    fd, filename = tempfile.mkstemp(prefix='.caesar-panel-', suffix='.caddy', dir=CADDY.parent)
    candidate: Path | None = Path(filename)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(updated)
        run(['/usr/bin/caddy', 'validate', '--config', str(candidate), '--adapter', 'caddyfile'])
        if not args.apply:
            print('PANEL_CADDY_VALIDATED: no live changes')
            return
        BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(BACKUPS, 0o700)
        backup = BACKUPS / ('Caddyfile-before-panel-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.write_text(original)
        os.chmod(backup, 0o600)
        original_stat = CADDY.stat()
        os.chown(candidate, original_stat.st_uid, original_stat.st_gid)
        os.chmod(candidate, stat.S_IMODE(original_stat.st_mode))
        if CADDY.read_text() != original:
            raise RuntimeError('Caddyfile changed during validation; refusing overwrite')
        os.replace(candidate, CADDY)
        candidate = None
        try:
            run(['systemctl', 'reload', 'caddy'])
            run(['systemctl', 'is-active', '--quiet', 'caddy'])
        except Exception:
            restore = CADDY.with_name('.Caddyfile.caesar-panel-restore')
            restore.write_text(original)
            os.chown(restore, original_stat.st_uid, original_stat.st_gid)
            os.chmod(restore, stat.S_IMODE(original_stat.st_mode))
            os.replace(restore, CADDY)
            run(['systemctl', 'reload', 'caddy'])
            raise
        print('PANEL_CADDY_APPLIED: ' + str(backup))
    finally:
        if candidate is not None:
            candidate.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit('ERROR: ' + str(error))
