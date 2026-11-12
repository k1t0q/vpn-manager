#!/usr/bin/python3
"""Root-owned forced SSH command. Runs as vpn-deploy, never as root.

Only replaces an isolated preview release; never runs repository code, pip,
shell fragments or migrations. Dependencies and systemd unit are admin-owned.
"""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path('/srv/vpn-manager-stage')
RELEASES = ROOT / 'releases'
SERVICE = 'vpn-manager-stage.service'
REPO = 'k1t0q/vpn-manager'
BRANCH = 'awg-migration-2026-11'
LIMIT = 20 * 1024 * 1024


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError('Download too large')
    return data


def unpack(data, destination):
    """Reject links, path traversal, special files and archive bombs."""
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        total = 0
        entries = []
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or len(path.parts) < 1:
                raise ValueError('Unsafe archive path')
            if not (member.isdir() or member.isfile()):
                raise ValueError('Archive links/special files forbidden')
            total += member.size
            if total > LIMIT or len(members) > 5000:
                raise ValueError('Archive too large')
            relative = Path(*path.parts[1:])
            target = destination / relative
            if destination.resolve() not in (target.resolve(), *target.resolve().parents):
                raise ValueError('Unsafe archive target')
            entries.append((member, target))
        destination.mkdir(mode=0o755)
        for member, target in entries:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError('Invalid archive file')
                with source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o644)


def release_path(sha):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Invalid release ID')
    return RELEASES / sha


def current():
    pointer = ROOT / 'current'
    if not pointer.is_symlink():
        return None
    target = pointer.resolve()
    if target.parent != RELEASES.resolve() or not re.fullmatch('[0-9a-f]{40}', target.name):
        raise ValueError('Invalid current release')
    return target


def switch(target):
    pointer = ROOT / 'next'
    if pointer.exists() or pointer.is_symlink():
        pointer.unlink()
    pointer.symlink_to(target)
    os.replace(pointer, ROOT / 'current')


def restart():
    subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'restart', SERVICE],
                   check=True, timeout=30, capture_output=True)


def healthy():
    for _ in range(15):
        try:
            with urllib.request.urlopen('http://127.0.0.1:5053/health', timeout=2) as r:
                if r.status == 200 and json.load(r).get('ok') is True:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False


def deploy(sha, rollback=False):
    target = release_path(sha)
    previous = current()
    if rollback:
        if not (target / '.healthy').is_file():
            raise ValueError('Only previously healthy releases may be restored')
    else:
        # A stolen preview key cannot deploy an arbitrary repository/ref.
        branch = json.loads(fetch('https://api.github.com/repos/' + REPO + '/branches/' + BRANCH))
        if branch['commit']['sha'] != sha:
            raise ValueError('Release is not the migration branch head')
        if not target.exists():
            data = fetch('https://codeload.github.com/' + REPO + '/tar.gz/' + sha)
            temporary = ROOT / ('incoming-' + sha)
            if temporary.exists():
                shutil.rmtree(temporary)
            try:
                unpack(data, temporary)
                required = ['server/staging_api.py', 'index.html', 'sqlite-manager.js']
                if not all((temporary / p).is_file() for p in required):
                    raise ValueError('Incomplete release')
                expected = Path('/etc/vpn-manager-stage.requirements.sha256').read_text().strip()
                actual = hashlib.sha256((temporary / 'server/requirements.txt').read_bytes()).hexdigest()
                if actual != expected:
                    raise ValueError('Dependency changes need administrator review')
                # Only these public assets are served; backend source is not exposed.
                public = temporary / 'public'
                public.mkdir()
                for name in ['index.html', 'sqlite-manager.js', 'styles.css', 'recovery-core.js', 'manifest.webmanifest', 'sw.js']:
                    source = temporary / name
                    if source.is_file():
                        shutil.copyfile(source, public / name)
                (public / 'config.js').write_text('window.VPN_MANAGER_CONFIG={API_BASE:"/stage-api"};\n')
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
    switch(target)
    try:
        restart()
        if not healthy():
            raise ValueError('Preview health check failed')
    except Exception:
        if previous is not None:
            switch(previous)
            restart()
            if not healthy():
                raise RuntimeError('Preview rollback health check failed') from None
        else:
            (ROOT / 'current').unlink()
        raise
    (target / '.healthy').touch(mode=0o600)
    print(json.dumps({'ok': True, 'release': sha, 'previous': previous.name if previous else None}))


def main():
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    match = re.fullmatch(r'(deploy|rollback) ([0-9a-f]{40})', command)
    if not match:
        raise ValueError('Only deploy/rollback with a full commit SHA is allowed')
    ROOT.mkdir(mode=0o750, exist_ok=True)
    RELEASES.mkdir(mode=0o755, exist_ok=True)
    with (ROOT / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        deploy(match[2], rollback=match[1] == 'rollback')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No URLs, environment values, subprocess stderr or secrets in logs.
        print('Preview deployment failed: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
