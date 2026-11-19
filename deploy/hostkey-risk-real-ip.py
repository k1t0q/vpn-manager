"""Guarded, opt-in Xray XFF source-IP fix for a Caddy-only localhost WS inbound.

Plan is read-only. Apply requires an explicit restart confirmation, preserves
all keys, creates a private backup, validates Xray and rolls back on failure.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from xray_hostkey import find_inbound, locked, MigrationError  # noqa: E402


XRAY = Path('/usr/local/etc/xray/config.json')
CADDY = Path('/etc/caddy/Caddyfile')
BACKUPS = Path('/root/caesar-xray-backups')
XRAY_BINARY = '/usr/local/bin/xray'
MARKER = 'X-Forwarded-Proto'  # Caddy sets this header, not the client.


class PreflightError(Exception):
    pass


def verify_caddy(text: str) -> None:
    """Refuse unexpected routing/header customizations; never edit Caddy."""
    pattern = re.compile(r'(?m)^\s*handle /vless-ws \{\s*$')
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise PreflightError('Expected exactly one Caddy /vless-ws handler')
    start = matches[0].end()
    depth = 1
    end = start
    while end < len(text) and depth:
        if text[end] == '{':
            depth += 1
        elif text[end] == '}':
            depth -= 1
        end += 1
    if depth:
        raise PreflightError('Unbalanced Caddy /vless-ws handler')
    block = text[start:end-1]
    if len(re.findall(r'(?m)^\s*reverse_proxy\s+127\.0\.0\.1:10000\s*(?:\{|$)', block)) != 1:
        raise PreflightError('Unexpected reverse proxy for VLESS WebSocket')
    if re.search(r'(?im)\bheader_up\s+[-+]?X-Forwarded-(?:For|Proto)\b', block):
        raise PreflightError('Caddy overrides a required proxy header; manual review needed')


def proposed_config(current: dict) -> tuple[dict, bool]:
    candidate = copy.deepcopy(current)
    inbound = find_inbound(candidate)
    stream = inbound['streamSettings']
    sockopt = stream.get('sockopt')
    if sockopt is None:
        sockopt = {}
        stream['sockopt'] = sockopt
    if not isinstance(sockopt, dict):
        raise PreflightError('Unexpected Xray socket options')
    existing = sockopt.get('trustedXForwardedFor')
    if existing not in (None, [], [MARKER]):
        raise PreflightError('Xray already has different trusted XFF rules; do not overwrite')
    if existing == [MARKER]:
        return current, False
    sockopt['trustedXForwardedFor'] = [MARKER]
    return candidate, True


def command(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise PreflightError(f'{args[0]} {args[1] if len(args) > 1 else ""} failed: '
                             + result.stderr[-300:])
    return result


def xray_test(candidate: dict) -> None:
    fd, name = tempfile.mkstemp(prefix='.caesar-xray-realip-', suffix='.json', dir=XRAY.parent)
    proposed = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(candidate, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        command(XRAY_BINARY, 'run', '-test', '-config', str(proposed), timeout=40)
    finally:
        proposed.unlink(missing_ok=True)


def atomic_bytes(path: Path, payload: bytes, original: os.stat_result) -> None:
    fd, filename = tempfile.mkstemp(prefix='.caesar-risk-realip-', suffix='.json', dir=path.parent)
    temp = Path(filename)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, stat.S_IMODE(original.st_mode))
        os.chown(temp, original.st_uid, original.st_gid)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def backup(source: bytes) -> Path:
    if BACKUPS.is_symlink():
        raise PreflightError('Unexpected Xray backup symlink')
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = (datetime.now(timezone.utc).strftime('config-before-risk-realip-%Y%m%d-%H%M%S-')
            + hashlib.sha256(source).hexdigest()[:10] + '.json')
    destination = BACKUPS / name
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(source)
        stream.flush()
        os.fsync(stream.fileno())
    return destination


def plan() -> tuple[bytes, os.stat_result, dict, bool]:
    if XRAY.is_symlink() or CADDY.is_symlink():
        raise PreflightError('Unexpected config symlink')
    if not XRAY.is_file() or not CADDY.is_file():
        raise PreflightError('Xray or Caddy configuration missing')
    verify_caddy(CADDY.read_text(encoding='utf-8'))
    version_line = command(XRAY_BINARY, 'version').stdout.splitlines()[0]
    version = re.match(r'^Xray\s+(\d+)\.(\d+)\.(\d+)', version_line)
    if not version:
        raise PreflightError('Cannot verify installed Xray version')
    major, minor, patch = map(int, version.groups())
    if (major, minor, patch) < (26, 6, 22):
        raise PreflightError('Xray version is too old for verified trusted XFF behavior')
    source = XRAY.read_bytes()
    original = XRAY.stat()
    current = json.loads(source)
    candidate, change = proposed_config(current)
    if change:
        xray_test(candidate)
    return source, original, candidate, change


def run(apply: bool) -> dict:
    if os.geteuid() != 0:
        raise PreflightError('Run as root on HOSTKEY')
    for name in ('xray', 'caddy', 'caesar-xray-expiry.timer', 'caesar-hostkey-risk.timer'):
        command('systemctl', 'is-active', '--quiet', name)
    # Same lock as the five-minute/fast Xray key reconciliation.
    with locked():
        source, original, candidate, change = plan()
        result = {'ok': True, 'change_needed': change,
                  'new_xray_setting': 'vless-ws.streamSettings.sockopt.trustedXForwardedFor',
                  'trusted_header': MARKER, 'caddy_untouched': True, 'keys_untouched': True,
                  'requires_brief_xray_restart': change}
        if not apply or not change:
            return {**result, 'dry_run': not apply, 'applied': False}
        target = (json.dumps(candidate, ensure_ascii=False, indent=2) + '\n').encode()
        previous_hash = hashlib.sha256(source).digest()
        if hashlib.sha256(XRAY.read_bytes()).digest() != previous_hash:
            raise PreflightError('Xray config changed during preflight; retry')
        saved = backup(source)
        try:
            atomic_bytes(XRAY, target, original)
            command('systemctl', 'restart', 'xray', timeout=60)
            command('systemctl', 'is-active', '--quiet', 'xray')
            command('systemctl', 'is-active', '--quiet', 'caddy')
            command('systemctl', 'is-active', '--quiet', 'caesar-xray-expiry.timer')
            command('systemctl', 'is-active', '--quiet', 'caesar-hostkey-risk.timer')
            verify = command(sys.executable, str(Path(__file__).parent / 'hostkey-expiry-verify.py'))
            if not json.loads(verify.stdout)['ok']:
                raise PreflightError('Expiry key integrity check failed')
        except Exception:
            atomic_bytes(XRAY, source, original)
            try:
                command('systemctl', 'restart', 'xray', timeout=60)
            except Exception as error:
                raise PreflightError('URGENT: automatic Xray rollback restart failed; '
                                     'inspect systemctl status xray') from error
            raise PreflightError('Change rolled back; original Xray config and keys restored') from None
        return {**result, 'dry_run': False, 'applied': True, 'backup': str(saved)}


def main() -> None:
    parser = argparse.ArgumentParser(description='Guarded source-IP fix for CAESAR VLESS behind Caddy')
    parser.add_argument('operation', choices=('plan', 'apply'))
    parser.add_argument('--confirm-vpn-restart', action='store_true')
    args = parser.parse_args()
    if args.operation == 'apply' and not args.confirm_vpn_restart:
        parser.error('Restart approval required: --confirm-vpn-restart')
    try:
        print(json.dumps(run(args.operation == 'apply'), ensure_ascii=False, indent=2))
    except (PreflightError, MigrationError, OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        parser.exit(2, f'SAFE STOP: {error}\n')


if __name__ == '__main__':
    main()
