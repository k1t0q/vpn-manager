"""Recoverable peer removal; client and history rows are retained as a tombstone."""
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import uuid
from awg_persistence import PeerStore, StateError, atomic_write, peer_blocks, sync_dir


class Deleter(PeerStore):
    def delete(self, cid, request_id, allow_protected=False):
        cid=str(cid)
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',request_id):
            raise StateError('A stable idempotency key is required')
        if cid in self.protected and not allow_protected:
            raise StateError('Existing client requires explicit authorization')
        payload=hashlib.sha256(json.dumps(['delete',cid]).encode()).hexdigest()
        with self.locked():
            self._recover()
            with self.connect() as con:
                self._schema(con)
                old=con.execute('SELECT * FROM awg_operations WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old['payload_hash']!=payload:raise StateError('Idempotency key reused')
                    return json.loads(old['result'])
                con.execute('BEGIN IMMEDIATE')
                con.execute('CREATE TABLE IF NOT EXISTS awg_deleted_clients(client_id INTEGER PRIMARY KEY,deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
                row=con.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone()
                if row is None:raise StateError('Client not found')
                if con.execute('SELECT 1 FROM awg_deleted_clients WHERE client_id=?',(cid,)).fetchone():
                    raise StateError('Client already removed')
                path=self._metadata(cid)
                before_meta=path.read_text();meta=json.loads(before_meta)
                if str(meta['client_id'])!=cid:raise StateError('Metadata client mismatch')
                public=meta['public_key']
                address=str(ipaddress.ip_address(meta['address']))+'/32'
                before=self.config.read_text();header,blocks=peer_blocks(before)
                if re.search(r'(?im)^\s*SaveConfig\s*=\s*true',header):
                    raise StateError('SaveConfig must be disabled before managed writes')
                matching=[(fields,block) for key,fields,block in blocks if key==public]
                block=matching[0][1] if matching else None
                if matching and matching[0][0].get('AllowedIPs')!=address:
                    raise StateError('Client address mismatch')
                if (public in self.runtime.peers())!=bool(block):
                    raise StateError('Target disk/runtime drift requires review')
                after=header+''.join(block for key,_,block in blocks if key!=public)
                backup=self.state/('op-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
                if any(item.is_symlink() for item in path.parent.rglob('*')):
                    raise StateError('Symlink profile rejected')
                material=backup/'removed-profile';shutil.copytree(path.parent,material)
                tombstone=self.profiles/('.removed-'+uuid.uuid4().hex)
                for item in material.rglob('*'):
                    if item.is_symlink():raise StateError('Symlink profile rejected')
                    os.chmod(item,0o700 if item.is_dir() else 0o600)
                    if item.is_file():
                        with open(item,'rb') as handle:os.fsync(handle.fileno())
                os.chmod(material,0o700);sync_dir(material)
                with sqlite3.connect(backup/'clients.db') as target:
                    with sqlite3.connect(self.db_path) as source:source.backup(target)
                os.chmod(backup/'clients.db',0o600)
                journal=dict(request_id=request_id,cid=cid,public_key=public,
                             before_block=block,after_block=None,
                             before_config=before,after_config=after,
                             before_metadata=before_meta,after_metadata=None,
                             new_profile_backup=str(material),profile_tombstone=str(tombstone))
                atomic_write(backup/'journal.json',json.dumps(journal).encode())
                atomic_write(self.state/'pending.json',json.dumps(journal).encode())
                self.fault('prepared')
                atomic_write(self.config,after.encode());self.fault('config_written')
                self.runtime.apply(public,None);self.fault('runtime_applied')
                os.replace(path.parent,tombstone)
                sync_dir(self.profiles);sync_dir(backup);self.fault('profile_archived')
                con.execute('INSERT INTO awg_deleted_clients(client_id) VALUES(?)',(cid,))
                con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',
                            (row['user_id'],cid,'deleted','VPN profile removed; client and history retained in archive'))
                result=dict(ok=True,id=cid,deleted=True,history_preserved=True)
                con.execute('INSERT INTO awg_operations(request_id,payload_hash,result) VALUES(?,?,?)',
                            (request_id,payload,json.dumps(result)))
                con.commit();self.fault('db_committed')
                shutil.rmtree(tombstone);sync_dir(self.profiles)
                (self.state/'pending.json').rename(backup/'committed.json');sync_dir(self.state)
                return result
