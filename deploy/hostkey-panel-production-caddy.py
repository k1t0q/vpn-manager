"""Replace only the HOSTKEY VPN MANAGER pilot proxy with production routes.

The VLESS WebSocket and client subscription handlers must remain byte-for-byte
unchanged. Apply validates the Caddyfile, backs it up, and rolls back on error.
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


CADDYFILE = Path('/etc/caddy/Caddyfile')
BACKUPS = Path('/root/caesar-caddy-backups')
OLD_START = '    # CAESAR_MANAGER_PILOT_BEGIN\n'
OLD_END = '    # CAESAR_MANAGER_PILOT_END\n'
NEW_START = '    # CAESAR_MANAGER_PRODUCTION_BEGIN\n'
NEW_END = '    # CAESAR_MANAGER_PRODUCTION_END\n'
OLD_BLOCK = (OLD_START +
             '    handle /manager-test/* {\n'
             '        reverse_proxy 127.0.0.1:5059\n'
             '    }\n' + OLD_END)
NEW_BLOCK = (NEW_START +
             '    handle /manager/* {\n'
             '        reverse_proxy 127.0.0.1:5059\n'
             '    }\n'
             '    handle /manager {\n'
             '        redir /manager/ 308\n'
             '    }\n'
             '    handle /manager-test/* {\n'
             '        reverse_proxy 127.0.0.1:5059\n'
             '    }\n'
             '    handle /manager-test {\n'
             '        redir /manager/ 308\n'
             '    }\n' + NEW_END)


def plan(original: str) -> tuple[str, bool]:
    if '193.107.211.46.sslip.io, vpn.caesarbot.ru {' not in original:
        raise ValueError('Unexpected HOSTKEY site')
    if original.count('    # CAESAR_VLESS_ROUTES_BEGIN\n') != 1 or original.count('    # CAESAR_VLESS_ROUTES_END\n') != 1:
        raise ValueError('Subscription route markers changed')
    if 'handle /vless-ws {' not in original or 'reverse_proxy 127.0.0.1:10000' not in original:
        raise ValueError('Xray proxy changed')
    if original.count(NEW_BLOCK) == 1 and OLD_START not in original and OLD_END not in original:
        return original, False
    if original.count(OLD_BLOCK) != 1 or NEW_START in original or NEW_END in original:
        raise ValueError('Unknown VPN MANAGER routing; refusing replacement')
    if 'handle /manager/*' in original or 'handle /manager {' in original:
        raise ValueError('Unmanaged production route already exists')
    return original.replace(OLD_BLOCK, NEW_BLOCK, 1), True


def run(args: list[str]) -> None:
    result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'command failed')[-1200:])


def install() -> str:
    source = CADDYFILE.read_text()
    candidate_text, changed = plan(source)
    if not changed:
        return 'PRODUCTION_CADDY_ALREADY_ACTIVE'
    fd, path = tempfile.mkstemp(prefix='.caesar-manager-production-', suffix='.caddy', dir=CADDYFILE.parent)
    candidate = Path(path)
    source_stat = CADDYFILE.stat()
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(candidate_text)
        os.chown(candidate, source_stat.st_uid, source_stat.st_gid)
        os.chmod(candidate, stat.S_IMODE(source_stat.st_mode))
        run(['/usr/bin/caddy', 'validate', '--config', str(candidate), '--adapter', 'caddyfile'])
        BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(BACKUPS, 0o700)
        backup = BACKUPS / ('Caddyfile-before-manager-production-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.write_text(source)
        os.chmod(backup, 0o600)
        if CADDYFILE.read_text() != source:
            raise RuntimeError('Caddyfile changed during validation')
        os.replace(candidate, CADDYFILE)
        candidate = None
        try:
            run(['systemctl', 'reload', 'caddy'])
            run(['systemctl', 'is-active', '--quiet', 'caddy'])
        except Exception:
            restore = CADDYFILE.with_name('.Caddyfile.caesar-production-restore')
            restore.write_text(source)
            os.chown(restore, source_stat.st_uid, source_stat.st_gid)
            os.chmod(restore, stat.S_IMODE(source_stat.st_mode))
            os.replace(restore, CADDYFILE)
            run(['systemctl', 'reload', 'caddy'])
            raise
        return f'PRODUCTION_CADDY_READY: backup={backup}'
    finally:
        if candidate is not None:
            candidate.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and os.geteuid() != 0:
        parser.error('Root required')
    if args.apply:
        print(install())
    else:
        candidate, changed = plan(CADDYFILE.read_text())
        if not changed:
            print('PRODUCTION_CADDY_ALREADY_ACTIVE')
            return
        fd, path = tempfile.mkstemp(prefix='.caesar-manager-check-', suffix='.caddy', dir=CADDYFILE.parent)
        try:
            with os.fdopen(fd, 'w') as handle:
                handle.write(candidate)
            run(['/usr/bin/caddy', 'validate', '--config', path, '--adapter', 'caddyfile'])
        finally:
            Path(path).unlink(missing_ok=True)
        print('PRODUCTION_CADDY_VALIDATED: no changes')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        sys.exit(f'ERROR: {exc}')
