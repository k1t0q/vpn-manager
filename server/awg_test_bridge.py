"""Privileged, frozen test-peer API. Existing peers are always protected.

The installer supplies a private staging database/profile root and immutable
baseline. This module is never imported by the unprivileged panel application.
"""
import json
import re
import sqlite3
import subprocess
from awg_persistence import StateError, peer_blocks
from awg_provision import Provisioner
from awg_delete import Deleter


class TestBridge:
    def __init__(self, store, template, baseline):
        self.store=store
        self.template=template
        self.baseline=baseline

    def guard(self):
        _,blocks=peer_blocks(self.store.config.read_text())
        current={key:fields for key,fields,_ in blocks}
        if any(current.get(key)!=fields for key,fields in self.baseline['peers'].items()):
            raise StateError('Protected peer configuration changed; manual review required')
        if not set(self.baseline['runtime_peers']).issubset(self.store.runtime.peers()):
            raise StateError('Protected runtime peers changed; manual review required')

    def dispatch(self, method, path, data=None, request_id=''):
        if method=='POST' and not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',request_id):
            raise StateError('A stable idempotency key is required')
        if method=='POST' and path=='/api/managed/create':
            if not isinstance(data,dict):raise StateError('Invalid client details')
            if not str(data.get('name','')).startswith('STAGING TEST '):
                raise StateError('Test client name must start with STAGING TEST ')
            if data.get('referrer_id') is not None and str(data['referrer_id']) in self.store.protected:
                raise StateError('Existing subscribers cannot receive staging test bonuses')
            with self.store.connect() as con:
                # Limit staging to three test clients. A committed retry is allowed.
                prior=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_operations'").fetchone()
                retry=prior and con.execute('SELECT 1 FROM awg_operations WHERE request_id=?',(request_id,)).fetchone()
                deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
                rows=con.execute('SELECT id FROM clients'+(' WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients)' if deleted else '')).fetchall()
                if not retry and sum(str(row[0]) not in self.store.protected for row in rows)>=3:
                    raise StateError('Three staging test clients already exist')
            self.guard()
            return self.store.create(data,request_id,self.template),201,'application/json'
        match=re.fullmatch(r'/api/wg/client/([0-9]{1,10})/(enable|disable|delete|config|qr)',path)
        if not match:raise StateError('Unsupported test operation')
        cid,action=match.groups()
        if cid in self.store.protected:
            raise StateError('Existing client is protected from staging operations')
        if method=='GET' and action in ('config','qr'):
            path=self.store._metadata(cid).parent/'client.conf'
            if path.is_symlink():raise StateError('Symlink profile rejected')
            material=path.read_bytes()
            if action=='config':return material,200,'application/octet-stream'
            try:
                result=subprocess.run(['qrencode','-t','SVG','-o','-'],input=material,
                                      capture_output=True,check=True,timeout=10)
            except (OSError,subprocess.SubprocessError):
                raise StateError('QR encoder unavailable') from None
            return result.stdout,200,'image/svg+xml'
        if method!='POST' or action not in ('enable','disable','delete'):
            raise StateError('Unsupported test method')
        self.guard()
        if action=='delete':
            deleter=Deleter(self.store.db_path,self.store.config,self.store.profiles,
                            self.store.state,self.store.runtime,protected_ids=self.store.protected)
            return deleter.delete(cid,request_id),200,'application/json'
        return self.store.toggle(cid,action=='enable',request_id),200,'application/json'
