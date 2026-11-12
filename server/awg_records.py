"""SQLite-only import and delivery records; never creates or rewrites a profile."""
from datetime import date, datetime, timezone
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import uuid
from awg_persistence import PeerStore, StateError, billing_date, peer_blocks, subscription_deadline


class Records(PeerStore):
    def _transaction(self, kind, data, request_id, apply):
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', request_id):
            raise StateError('A stable idempotency key is required')
        payload = hashlib.sha256(json.dumps([kind, data], sort_keys=True).encode()).hexdigest()
        with self.locked():
            self._recover()
            with self.connect() as con:
                self._schema(con)
                previous = con.execute('SELECT * FROM awg_operations WHERE request_id=?', (request_id,)).fetchone()
                if previous:
                    if previous['payload_hash'] != payload:
                        raise StateError('Idempotency key reused')
                    return json.loads(previous['result'])
                backup = self.state/('records-'+uuid.uuid4().hex)
                backup.mkdir(mode=0o700)
                with sqlite3.connect(backup/'clients.db') as target:
                    with sqlite3.connect(self.db_path) as source:
                        source.backup(target)
                os.chmod(backup/'clients.db', 0o600)
                con.execute('BEGIN IMMEDIATE')
                result = apply(con)
                self.fault('before_db_commit')
                con.execute('INSERT INTO awg_operations(request_id,payload_hash,result) VALUES(?,?,?)',
                            (request_id, payload, json.dumps(result)))
                con.commit()
                self.fault('db_committed')
                return result

    @staticmethod
    def _active(con, cid):
        row = con.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone()
        archived = con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
        if row is None or (archived and con.execute('SELECT 1 FROM awg_deleted_clients WHERE client_id=?', (cid,)).fetchone()):
            raise StateError('Client not found or archived')
        return row

    def delivery(self, cid, delivered, request_id, now=None):
        if type(delivered) is not bool or not re.fullmatch(r'[0-9]{1,10}', str(cid)):
            raise StateError('Invalid delivery status')
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise StateError('Timezone-aware clock required')
        def apply(con):
            row = self._active(con, cid)
            con.execute('CREATE TABLE IF NOT EXISTS profile_delivery(client_id INTEGER PRIMARY KEY,delivered_at TEXT)')
            stamp = now.isoformat() if delivered else None
            con.execute('INSERT INTO profile_delivery(client_id,delivered_at) VALUES(?,?) ON CONFLICT(client_id) DO UPDATE SET delivered_at=excluded.delivered_at', (cid, stamp))
            con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',
                        (row['user_id'], cid, 'profile_delivered' if delivered else 'profile_delivery_reset',
                         'Administrator marked VPN profile as delivered' if delivered else 'Administrator cleared profile delivery mark'))
            return dict(ok=True, id=str(cid), delivered_at=stamp)
        return self._transaction('delivery', [str(cid), delivered], request_id, apply)

    def _candidates(self, con):
        ids = {str(row[0]) for row in con.execute('SELECT id FROM clients')}
        links = {str(row[0]) for row in con.execute('SELECT wg_client_id FROM clients WHERE wg_client_id IS NOT NULL')}
        _, blocks = peer_blocks(self.config.read_text())
        disk = {key: fields for key, fields, _ in blocks}
        live = self.runtime.peers()
        result = []
        keys, addresses = set(), set()
        for folder in sorted(self.profiles.iterdir()):
            if not folder.name.isdigit():
                continue
            path = self._metadata(folder.name)
            if path.is_symlink():
                raise StateError('Symlink metadata rejected')
            meta = json.loads(path.read_text())
            cid = str(meta['client_id'])
            address = str(ipaddress.ip_address(meta['address']))
            public = meta['public_key']
            if cid != folder.name or public in keys or address in addresses:
                raise StateError('Duplicate or inconsistent profile mapping')
            keys.add(public); addresses.add(address)
            if cid in ids:
                continue
            if cid in links or not re.fullmatch(r'[A-Za-z0-9+/]{43}=', public):
                raise StateError('Ambiguous imported profile mapping')
            profile = folder/'client.conf'
            if not profile.is_file() or profile.is_symlink():
                raise StateError('Original imported profile unavailable')
            fields = disk.get(public)
            if fields is None:
                if meta.get('enabled') is not False or public in live:
                    raise StateError('Imported disabled profile state is inconsistent')
                _, saved = peer_blocks(meta.get('disabled_peer_block', ''))
                if len(saved) != 1 or saved[0][0] != public:
                    raise StateError('Imported disabled peer block unavailable')
                fields = saved[0][1]
            elif public not in live or meta.get('enabled') is False:
                raise StateError('Imported profile disk/runtime drift')
            if fields.get('AllowedIPs') != address+'/32':
                raise StateError('Imported address mismatch')
            name = meta.get('name')
            if not isinstance(name, str) or not name.strip() or len(name) > 150:
                raise StateError('Imported name requires review')
            period = meta.get('period') or 'imported'
            if period not in {'1','6','12','1m','2m','6m','12m','trial','14d','lifetime','imported'}:
                raise StateError('Imported tariff requires review')
            end = meta.get('end_date') if period != 'lifetime' else None
            subscription_deadline(period, end)
            result.append(dict(id=int(cid), name=name, address=address, public_key=public,
                               period=period, end_date=end, peer_fields=fields, enabled=public in live))
        return result

    def candidates(self):
        with self.locked():
            self._recover()
            with self.connect() as con:
                return [{k:v for k,v in item.items() if k not in ('public_key','peer_fields')} for item in self._candidates(con)]

    def import_profiles(self, client_ids, request_id, now=None):
        if not isinstance(client_ids, list) or not 1 <= len(client_ids) <= 100 or any(type(cid) is not int or cid <= 0 for cid in client_ids) or len(set(client_ids)) != len(client_ids):
            raise StateError('Select distinct numeric profile IDs')
        client_ids = sorted(client_ids)
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise StateError('Timezone-aware clock required')
        def apply(con):
            candidates = {item['id']:item for item in self._candidates(con)}
            if set(client_ids)-set(candidates):
                raise StateError('Selected profiles are no longer import candidates')
            owner = getattr(self, 'owner_id', None)
            if not owner or not con.execute('SELECT 1 FROM clients WHERE user_id=?', (owner,)).fetchone():
                raise StateError('Trusted existing owner required')
            con.execute('CREATE TABLE IF NOT EXISTS awg_imported_profiles(client_id INTEGER PRIMARY KEY,public_key TEXT UNIQUE NOT NULL,address TEXT UNIQUE NOT NULL,peer_fields TEXT NOT NULL)')
            for cid in client_ids:
                item = candidates[cid]
                con.execute('INSERT INTO clients(id,user_id,name,phone,start_date,end_date,period,note,referrer_id,wg_client_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (cid, owner, item['name'], '', billing_date(now).isoformat(), item['end_date'], item['period'], 'Imported existing AmneziaWG profile', None, str(cid)))
                con.execute('INSERT INTO awg_imported_profiles VALUES(?,?,?,?)',
                            (cid, item['public_key'], item['address'], json.dumps(item['peer_fields'])))
                con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',
                            (owner, cid, 'imported', 'Existing AWG profile linked; keys, address and configuration preserved'))
            return dict(ok=True, imported=client_ids, profiles_unchanged=True)
        return self._transaction('import', client_ids, request_id, apply)
