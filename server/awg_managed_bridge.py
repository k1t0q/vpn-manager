"""Production controller, deliberately NOT enabled by staging deployments.

An owner-installed release must stop the staging writer before using this
controller. Existing keys/addresses and unmanaged runtime peers stay invariant.
"""
import json
import re
from awg_persistence import StateError, peer_blocks
from awg_test_bridge import TestBridge
from awg_subscriptions import Subscriptions
from awg_records import Records


class ManagedBridge(TestBridge):
    def __init__(self, store, template, baseline):
        super().__init__(store, template, baseline)
        # All records belong to this explicitly authorized single admin panel;
        # historic user_id values are preserved, including mixed legacy owners.
        self.store.cross_owner_referrals = True

    def guard(self):
        _, blocks = peer_blocks(self.store.config.read_text())
        disk = {key: fields for key, fields, _ in blocks}
        live = self.store.runtime.peers()
        with self.store.connect() as con:
            archived = con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
            deleted = {str(r[0]) for r in con.execute('SELECT client_id FROM awg_deleted_clients')} if archived else set()
            rows = {str(r[0]) for r in con.execute('SELECT id FROM clients')}
            imported = con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_imported_profiles'").fetchone()
            expected_clients = dict(self.baseline['clients'])
            if imported:
                for row in con.execute('SELECT * FROM awg_imported_profiles'):
                    expected_clients[str(row['client_id'])] = dict(public_key=row['public_key'], address=row['address'], peer_fields=json.loads(row['peer_fields']))
        original_keys = set()
        for cid, expected in expected_clients.items():
            key = expected['public_key']
            original_keys.add(key)
            if cid in deleted:
                if key in disk or key in live:
                    raise StateError('Archived peer remains active; review required')
                continue
            if cid not in rows:
                raise StateError('Original client record missing')
            meta = json.loads(self.store._metadata(cid).read_text())
            if str(meta['client_id']) != cid or meta['public_key'] != key or meta['address'] != expected['address']:
                raise StateError('Original profile key or address changed')
            if key in disk:
                if disk[key] != expected['peer_fields'] or key not in live or meta.get('enabled') is False:
                    raise StateError('Original peer configuration or runtime drift')
            elif meta.get('enabled') is not False or key in live:
                raise StateError('Original disabled peer state is inconsistent')
        unmanaged = set(self.baseline['runtime_peers']) - original_keys
        if not unmanaged.issubset(live):
            raise StateError('Unmanaged runtime peer changed')
        for key, fields in self.baseline.get('unmanaged_disk_peers', {}).items():
            if key in original_keys:
                continue
            if disk.get(key) != fields:
                raise StateError('Unmanaged disk peer changed')

    def dispatch(self, method, path, data=None, request_id=''):
        if method == 'POST' and not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', request_id):
            raise StateError('A stable idempotency key is required')
        records = Records(self.store.db_path, self.store.config, self.store.profiles,
                          self.store.state, self.store.runtime, protected_ids=())
        records.owner_id = self.store.owner_id
        delivery = re.fullmatch(r'/api/managed/clients/([0-9]{1,10})/delivery', path)
        if method == 'POST' and delivery:
            if not isinstance(data, dict) or set(data) != {'delivered'}:
                raise StateError('Invalid delivery fields')
            return records.delivery(delivery[1], data['delivered'], request_id), 200, 'application/json'
        if path == '/api/managed/import':
            self.guard()
            if method == 'GET':
                return dict(ok=True, candidates=records.candidates()), 200, 'application/json'
            if method == 'POST':
                if not isinstance(data, dict) or set(data) != {'client_ids'}:
                    raise StateError('Invalid import fields')
                return records.import_profiles(data['client_ids'], request_id), 200, 'application/json'
        if method == 'POST' and path == '/api/managed/create':
            self.guard()
            return self.store.create(data, request_id, self.template), 201, 'application/json'
        subscription = re.fullmatch(r'/api/managed/clients/([0-9]{1,10})/(edit|extend|referral)', path)
        if method == 'POST' and subscription:
            self.guard()
            service = Subscriptions(self.store.db_path, self.store.config, self.store.profiles,
                                    self.store.state, self.store.runtime, protected_ids=())
            service.expiry_hold = self.baseline.get('initial_expiry_hold', {})
            service.cross_owner_referrals = True
            result = service.change(subscription[1], subscription[2], data, request_id)
            return result, 200, 'application/json'
        canonical_delete = re.fullmatch(r'/api/managed/clients/([0-9]{1,10})/delete', path)
        if method == 'POST' and canonical_delete:
            path = '/api/wg/client/'+canonical_delete[1]+'/delete'
        else:
            peer = re.fullmatch(r'/api/wg/client/([A-Za-z0-9_-]{1,128})/(enable|disable|delete|config|qr)', path)
            if peer:
                # Original billing IDs and legacy wg-easy IDs are different.
                # Never interpret a UUID as a profile directory or guess a mapping.
                identifier = peer[1]
                with self.store.connect() as con:
                    rows = con.execute('SELECT id FROM clients WHERE wg_client_id=? OR CAST(id AS TEXT)=?',
                                       (identifier, identifier)).fetchall()
                ids = {str(row['id']) for row in rows}
                if len(ids) != 1:
                    raise StateError('VPN client mapping is missing or ambiguous')
                path = '/api/wg/client/'+ids.pop()+'/'+peer[2]
        # Reuse the tested routing/QR/deletion engine without staging protection.
        return super().dispatch(method, path, data, request_id)

    def tick(self, now=None):
        self.guard()
        # Preserve the access of the original already-expired accounts until
        # their deadline changes through an explicit administrator operation.
        with self.store.connect() as con:
            rows = {str(r['id']): r['end_date'] for r in con.execute('SELECT id,end_date FROM clients')}
            releases = con.execute("SELECT 1 FROM sqlite_master WHERE name='initial_expiry_hold_releases'").fetchone()
            released = {str(row[0]) for row in con.execute('SELECT client_id FROM initial_expiry_hold_releases')} if releases else set()
        hold = [cid for cid, end in self.baseline.get('initial_expiry_hold', {}).items() if rows.get(cid) == end and cid not in released]
        service = Subscriptions(self.store.db_path, self.store.config, self.store.profiles,
                                self.store.state, self.store.runtime, protected_ids=hold)
        return service.tick(now)
