"""Create one private, verified HOSTKEY recovery archive without stopping VPN.

Run as root on the production VPS. Live SQLite files are copied through the
SQLite backup API, never through a raw copy of a possibly active WAL database.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
from zoneinfo import ZoneInfo


BACKUP_DIR = Path('/root/caesar-final-backups')
PROJECT_DIR = Path('/srv/caesar-manager-src')
FILES = {
    'var/lib/caesar-manager/clients.db': Path('/var/lib/caesar-manager/clients.db'),
    'var/lib/caesar-manager/push.db': Path('/var/lib/caesar-manager/push.db'),
    'var/lib/caesar-manager/panel-code': Path('/var/lib/caesar-manager/panel-code'),
    'var/lib/caesar-manager/push-private.pem': Path('/var/lib/caesar-manager/push-private.pem'),
    'var/lib/caesar-manager/push-public.key': Path('/var/lib/caesar-manager/push-public.key'),
    'etc/caesar/manager-panel.env': Path('/etc/caesar/manager-panel.env'),
    'usr/local/etc/xray/config.json': Path('/usr/local/etc/xray/config.json'),
    'etc/caddy/Caddyfile': Path('/etc/caddy/Caddyfile'),
    'etc/systemd/system/caesar-vless-panel.service': Path('/etc/systemd/system/caesar-vless-panel.service'),
    'etc/systemd/system/caesar-vless-subscriptions.service': Path('/etc/systemd/system/caesar-vless-subscriptions.service'),
    'etc/systemd/system/caesar-xray-expiry.service': Path('/etc/systemd/system/caesar-xray-expiry.service'),
    'etc/systemd/system/caesar-xray-expiry.timer': Path('/etc/systemd/system/caesar-xray-expiry.timer'),
    'etc/systemd/system/caesar-xray-fast-sync.path': Path('/etc/systemd/system/caesar-xray-fast-sync.path'),
    'etc/systemd/system/caesar-hostkey-push.service': Path('/etc/systemd/system/caesar-hostkey-push.service'),
    'etc/systemd/system/caesar-hostkey-push.timer': Path('/etc/systemd/system/caesar-hostkey-push.timer'),
}
SQLITE_FILES = frozenset(('var/lib/caesar-manager/clients.db', 'var/lib/caesar-manager/push.db'))


class BackupError(Exception):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_snapshot(source: Path, destination: Path) -> None:
    destination.touch(mode=0o600, exist_ok=False)
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=30) as live:
        with sqlite3.connect(destination, timeout=30) as copy:
            live.backup(copy)
    with sqlite3.connect(f'file:{destination}?mode=ro', uri=True) as check:
        if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise BackupError(f'SQLite integrity failed: {source}')
    os.chmod(destination, 0o600)


def require_regular_file(source: Path) -> int:
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise BackupError(f'Expected a regular file, not a symlink/device: {source}')
    return stat.S_IMODE(info.st_mode)


def client_counts(db: Path) -> dict:
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as con:
        total = con.execute('SELECT COUNT(*) FROM clients').fetchone()[0]
        archives = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='caesar_client_archives'").fetchone()
        visible = (con.execute('SELECT COUNT(*) FROM clients WHERE id NOT IN '
                               '(SELECT client_id FROM caesar_client_archives)').fetchone()[0]
                   if archives else total)
    if total <= 0:
        raise BackupError('Refusing backup of an empty clients database')
    return {'stored_clients': total, 'visible_clients': visible}


def git_commit(project_dir: Path) -> str:
    result = subprocess.run(['git', '-C', str(project_dir), 'rev-parse', '--verify', 'HEAD'],
                            capture_output=True, text=True, timeout=15, check=True)
    return result.stdout.strip()


def create_archive(output_dir: Path = BACKUP_DIR, files: dict[str, Path] | None = None,
                   project_dir: Path = PROJECT_DIR, include_code: bool = True) -> dict:
    files = FILES if files is None else files
    if output_dir.is_symlink():
        raise BackupError('Backup directory is a symlink')
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not output_dir.is_dir():
        raise BackupError('Backup directory is not a directory')
    os.chmod(output_dir, 0o700)
    timestamp = datetime.now(ZoneInfo('Europe/Moscow')).strftime('%Y%m%d-%H%M%S')
    final_path = output_dir / f'caesar-vpn-stable-{timestamp}-{secrets.token_hex(4)}.tar.gz'
    temporary_archive = output_dir / ('.' + final_path.name + '.tmp')
    archive_items: dict[str, dict] = {}
    counts: dict = {}
    previous_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix='.caesar-backup-staging-', dir=output_dir) as staging:
            stage = Path(staging)
            for relative, source in files.items():
                if relative.startswith('/') or '..' in Path(relative).parts:
                    raise BackupError(f'Unsafe archive path: {relative}')
                original_mode = require_regular_file(source)
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if relative in SQLITE_FILES:
                    sqlite_snapshot(source, destination)
                else:
                    shutil.copyfile(source, destination, follow_symlinks=False)
                    os.chmod(destination, 0o600)
                archive_items[relative] = {'sha256': sha256(destination),
                                           'size': destination.stat().st_size,
                                           'original_mode': oct(original_mode)}
                if relative == 'var/lib/caesar-manager/clients.db':
                    counts = client_counts(destination)
            commit = git_commit(project_dir) if include_code else None
            if include_code:
                source_bundle = stage / 'source' / 'vpn-manager-source.tar'
                source_bundle.parent.mkdir(mode=0o700)
                subprocess.run(['git', '-C', str(project_dir), 'archive', '--format=tar',
                                '--output', str(source_bundle), commit], check=True, timeout=30)
                os.chmod(source_bundle, 0o600)
                archive_items['source/vpn-manager-source.tar'] = {
                    'sha256': sha256(source_bundle), 'size': source_bundle.stat().st_size,
                    'original_mode': '0o600'}
            manifest = {'created_at_moscow': datetime.now(ZoneInfo('Europe/Moscow')).isoformat(),
                        'source_commit': commit, 'archive_private': True,
                        'sqlite_copied_consistently': True, **counts, 'files': archive_items}
            (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
            os.chmod(stage / 'manifest.json', 0o600)
            archive_items['manifest.json'] = {'sha256': sha256(stage / 'manifest.json')}
            with tarfile.open(temporary_archive, mode='w:gz') as archive:
                for relative in archive_items:
                    archive.add(stage / relative, arcname=relative, recursive=False)
            os.chmod(temporary_archive, 0o600)
            with tarfile.open(temporary_archive, 'r:gz') as archive:
                if set(archive.getnames()) != set(archive_items):
                    raise BackupError('Archive contents do not match snapshot manifest')
                for relative, info in archive_items.items():
                    stream = archive.extractfile(relative)
                    if stream is None or hashlib.sha256(stream.read()).hexdigest() != info['sha256']:
                        raise BackupError(f'Archive checksum mismatch: {relative}')
            os.replace(temporary_archive, final_path)
        return {'ok': True, 'archive': str(final_path), 'bytes': final_path.stat().st_size,
                'sha256': sha256(final_path), 'files': len(archive_items),
                'code_commit': commit, **counts, 'live_services_restarted': False}
    finally:
        temporary_archive.unlink(missing_ok=True)
        os.umask(previous_umask)


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('ERROR: Run as root on HOSTKEY')
    try:
        print(json.dumps(create_archive(), ensure_ascii=False, indent=2))
    except (BackupError, OSError, sqlite3.Error, subprocess.SubprocessError, tarfile.TarError) as error:
        raise SystemExit(f'ERROR: Backup was not completed: {error}') from error
