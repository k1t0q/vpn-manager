"""Per-peer AWG mutations with a durable undo journal and SQLite commit marker.

No syncconf, interface restarts, key rotation, or implicit startup mutations.
Call recover explicitly under the same lock before any mutation. Existing peers
are protected by default; callers must explicitly authorize their IDs.
"""
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid


class StateError(RuntimeError):
    pass


def atomic_write(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def peer_blocks(text):
    parts = re.split(r'(?m)^[ \t]*\[Peer\][ \t]*\r?$', text)
    blocks = []
    for body in parts[1:]:
        fields = {}
        for line in body.splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                fields[key.strip()] = value.strip()
        key = fields.get('PublicKey', '')
        if not re.fullmatch(r'[A-Za-z0-9+/]{43}=', key):
            raise StateError('Invalid peer block')
        if key in [k for k, _, _ in blocks]:
            raise StateError('Duplicate peer key')
        blocks.append((key, fields, '[Peer]' + body))
    return parts[0], blocks


class AWGRuntime:
    def __init__(self, interface='awg-test'):
        self.interface = interface

    def peers(self):
        result = self._run(['show', self.interface, 'peers'])
        return set(result.split())

    def allowed_ips(self):
        result = self._run(['show', self.interface, 'allowed-ips'])
        networks = []
        for line in result.splitlines():
            fields = line.split(None, 1)
            if len(fields) == 2 and fields[1] != '(none)':
                networks.extend(ipaddress.ip_network(n.strip(), strict=False)
                                for n in fields[1].split(','))
        return networks

    def server_public_key(self):
        return self._run(['show', self.interface, 'public-key']).strip()

    def new_keys(self):
        private = self._run(['genkey']).strip()
        try:
            result = subprocess.run(['awg', 'pubkey'], input=private+'\n',
                                    capture_output=True, text=True, timeout=10,
                                    check=True)
        except (subprocess.SubprocessError, OSError):
            raise StateError('AWG key generation failed') from None
        public = result.stdout.strip()
        psk = self._run(['genpsk']).strip()
        if not all(re.fullmatch(r'[A-Za-z0-9+/]{43}=', k)
                   for k in (private, public, psk)):
            raise StateError('AWG returned invalid key material')
        return private, public, psk

    def is_down(self):
        result = subprocess.run(['ip', 'link', 'show', 'dev', self.interface],
                                capture_output=True, timeout=10)
        return result.returncode == 1

    def _run(self, args):
        try:
            result = subprocess.run(['awg', *args], capture_output=True,
                                    text=True, timeout=10, check=True)
            return result.stdout
        except (subprocess.SubprocessError, OSError):
            # Command stderr and argv may contain key material. Never propagate.
            raise StateError('AWG command failed') from None

    def apply(self, public_key, block):
        if block is None:
            self._run(['set', self.interface, 'peer', public_key, 'remove'])
        else:
            _, peers = peer_blocks(block)
            if len(peers) != 1 or peers[0][0] != public_key:
                raise StateError('Invalid target peer')
            fields = peers[0][1]
            if set(fields) - {'PublicKey', 'AllowedIPs', 'PresharedKey',
                              'Endpoint', 'PersistentKeepalive'}:
                raise StateError('Unsupported peer fields')
            args = ['set', self.interface, 'peer', public_key,
                    'allowed-ips', fields['AllowedIPs']]
            if fields.get('Endpoint'):
                args += ['endpoint', fields['Endpoint']]
            if fields.get('PersistentKeepalive'):
                args += ['persistent-keepalive', fields['PersistentKeepalive']]
            if fields.get('PresharedKey'):
                fd, name = tempfile.mkstemp(prefix='awg-psk-')
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, 'w') as out:
                        out.write(fields['PresharedKey'] + '\n')
                    self._run([*args, 'preshared-key', name])
                finally:
                    os.unlink(name)
            else:
                self._run(args)
        if (public_key in self.peers()) != (block is not None):
            raise StateError('AWG target state not verified')


class PeerStore:
    def __init__(self, db_path, config_path, profile_root, state_root,
                 runtime=None, protected_ids=None, fault=None):
        self.db_path = Path(db_path)
        self.config = Path(config_path)
        self.profiles = Path(profile_root)
        self.state = Path(state_root)
        self.runtime = runtime or AWGRuntime()
        if protected_ids is None:
            with self.connect() as con:
                protected_ids = [r[0] for r in con.execute('SELECT id FROM clients')]
        self.protected = set(map(str, protected_ids))
        self.fault = fault or (lambda point: None)

    @contextlib.contextmanager
    def locked(self):
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.state.stat().st_mode & 0o077:
            raise StateError('State directory must be private')
        with open(self.state / 'lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def connect(self):
        con = sqlite3.connect(self.db_path.as_uri() + '?mode=rw', uri=True,
                              timeout=20)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        return con

    def _schema(self, con):
        con.execute('''CREATE TABLE IF NOT EXISTS awg_operations(
            request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
            result TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')

    def _metadata(self, cid):
        if not re.fullmatch(r'[0-9]{1,10}', str(cid)):
            raise StateError('Invalid client ID')
        folder = self.profiles / str(cid)
        if folder.is_symlink():
            raise StateError('Symlink profile rejected')
        return folder / 'metadata.json'

    def recover(self, offline=False):
        with self.locked():
            return self._recover(offline=offline)

    def _recover(self, offline=False):
        pending = self.state / 'pending.json'
        if not pending.exists():
            return 'clean'
        journal = json.loads(pending.read_text())
        with self.connect() as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_operations'").fetchone()
            committed = bool(exists and con.execute(
                'SELECT 1 FROM awg_operations WHERE request_id=?',
                (journal['request_id'],)).fetchone())
        direction = 'after' if committed else 'before'
        if offline:
            if not self.runtime.is_down():
                raise StateError('Offline recovery requires an absent interface')
        else:
            self.runtime.peers()  # Fail closed while AWG is unavailable.
            self.runtime.apply(journal['public_key'], journal[direction + '_block'])
        atomic_write(self.config, journal[direction + '_config'].encode())
        metadata = journal[direction + '_metadata']
        profile = self._metadata(journal['cid']).parent
        if metadata is None:
            # Aborted creation: retain generated material in a private archive,
            # remove it from the active profile directory, never rotate a live key.
            if profile.exists():
                shutil.move(str(profile), str(self.state / ('aborted-profile-' + uuid.uuid4().hex)))
                sync_dir(self.profiles)
        else:
            if not profile.exists() and journal.get('new_profile_backup'):
                backup = Path(journal['new_profile_backup'])
                if self.state.resolve() not in backup.resolve().parents:
                    raise StateError('Invalid profile recovery path')
                temporary = self.profiles / ('.recover-' + uuid.uuid4().hex)
                shutil.copytree(backup, temporary)
                os.replace(temporary, profile)
                sync_dir(self.profiles)
            atomic_write(self._metadata(journal['cid']), metadata.encode())
        for change in journal.get('related_metadata', []):
            atomic_write(self._metadata(change['cid']), change[direction].encode())
        pending.rename(self.state / ('recovered-' + uuid.uuid4().hex + '.json'))
        sync_dir(self.state)
        return direction

    def toggle(self, cid, enabled, request_id, allow_protected=False):
        if type(enabled) is not bool:
            raise StateError('Enabled must be boolean')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', request_id):
            raise StateError('A stable idempotency key is required')
        cid = str(cid)
        if cid in self.protected and not allow_protected:
            raise StateError('Existing client requires explicit authorization')
        payload = hashlib.sha256(json.dumps([cid, enabled]).encode()).hexdigest()
        with self.locked():
            self._recover()
            with self.connect() as con:
                self._schema(con)
                previous = con.execute('SELECT * FROM awg_operations WHERE request_id=?',
                                       (request_id,)).fetchone()
                if previous:
                    if previous['payload_hash'] != payload:
                        raise StateError('Idempotency key reused for another request')
                    return json.loads(previous['result'])
                con.execute('BEGIN IMMEDIATE')
                row = con.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone()
                if row is None:
                    raise StateError('Client not found')
                path = self._metadata(cid)
                before_meta = path.read_text()
                meta = json.loads(before_meta)
                if str(meta['client_id']) != cid:
                    raise StateError('Metadata client mismatch')
                address = str(ipaddress.ip_address(meta['address'])) + '/32'
                public = meta['public_key']
                before = self.config.read_text()
                header, blocks = peer_blocks(before)
                if re.search(r'(?im)^\s*SaveConfig\s*=\s*true', header):
                    raise StateError('SaveConfig must be disabled before managed writes')
                matches = [block for key, _, block in blocks if key == public]
                before_block = matches[0] if matches else None
                live = self.runtime.peers()
                if (public in live) != bool(before_block):
                    raise StateError('Target disk/runtime drift requires review')
                if before_block:
                    fields = next(fields for key, fields, _ in blocks if key == public)
                    if fields.get('AllowedIPs') != address:
                        raise StateError('Client address mismatch')
                saved = meta.get('disabled_peer_block')
                if enabled:
                    after_block = before_block or saved
                    if not after_block:
                        raise StateError('Original peer block unavailable')
                    _, restored = peer_blocks(after_block)
                    if len(restored) != 1 or restored[0][0] != public or restored[0][1].get('AllowedIPs') != address:
                        raise StateError('Saved peer does not match profile')
                else:
                    after_block = None
                    if before_block:
                        meta['disabled_peer_block'] = before_block
                after = header + ''.join(block for key, _, block in blocks if key != public)
                if after_block:
                    after = after.rstrip() + '\n\n' + after_block.rstrip() + '\n'
                meta['enabled'] = enabled
                after_meta = json.dumps(meta, ensure_ascii=False, indent=2)
                result = {'ok': True, 'id': cid, 'enabled': enabled}
                journal = dict(request_id=request_id, cid=cid, public_key=public,
                    before_block=before_block, after_block=after_block,
                    before_config=before, after_config=after,
                    before_metadata=before_meta, after_metadata=after_meta)
                # SQLite backup API handles WAL correctly. Backup before open write transaction.
                backup_dir = self.state / ('op-' + uuid.uuid4().hex)
                backup_dir.mkdir(mode=0o700)
                with sqlite3.connect(backup_dir / 'clients.db') as target:
                    with sqlite3.connect(self.db_path) as source:
                        source.backup(target)
                os.chmod(backup_dir / 'clients.db', 0o600)
                atomic_write(backup_dir / 'journal.json', json.dumps(journal).encode())
                atomic_write(self.state / 'pending.json', json.dumps(journal).encode())
                self.fault('prepared')
                atomic_write(self.config, after.encode())
                self.fault('config_written')
                atomic_write(path, after_meta.encode())
                self.fault('metadata_written')
                self.runtime.apply(public, after_block)
                self.fault('runtime_applied')
                con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',
                            (row['user_id'], cid, 'vpn_enabled' if enabled else 'vpn_disabled',
                             'VPN access enabled' if enabled else 'VPN access disabled'))
                con.execute('INSERT INTO awg_operations(request_id,payload_hash,result) VALUES(?,?,?)',
                            (request_id, payload, json.dumps(result)))
                con.commit()
                self.fault('db_committed')
                (self.state / 'pending.json').rename(backup_dir / 'committed.json')
                sync_dir(self.state)
                return result

