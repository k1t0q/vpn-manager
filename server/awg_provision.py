"""Atomic creation of a NEW peer; never regenerates any existing profile.

Not wired to production routes. Requires an explicitly selected server-side
template and trusted PeerStore paths. Private material never enters results/logs.
"""
import configparser
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import uuid
from awg_persistence import billing_date, subscription_deadline, PeerStore, StateError, atomic_write, peer_blocks, sync_dir

MONTHS={'1m':1,'2m':2,'6m':6,'12m':12}
BONUS={'1m':14,'6m':30,'12m':60}


def normalized(data):
    if not isinstance(data, dict) or set(data)-{'name','phone','note','period','start_date','referrer_id'}:
        raise StateError('Invalid creation fields')
    out={key:data.get(key,'') for key in ('name','phone','note','period','start_date')}
    if not all(isinstance(out[k],str) for k in out):
        raise StateError('Creation fields must be strings')
    out['name']=out['name'].strip()
    if not out['name'] or len(out['name'])>150 or len(out['phone'])>100 or len(out['note'])>2000:
        raise StateError('Invalid client details')
    if out['period'] not in {*MONTHS,'14d','trial','lifetime'}:
        raise StateError('Invalid subscription period')
    try:date.fromisoformat(out['start_date'])
    except ValueError:raise StateError('Invalid start date') from None
    ref=data.get('referrer_id')
    if ref is not None and (type(ref) is not int or ref<=0):
        raise StateError('Invalid referrer')
    out['referrer_id']=ref
    return out


def deadline(start,period,now):
    if period=='lifetime':return None
    if period=='trial':return (now+timedelta(hours=24)).isoformat()
    if period=='14d':return (start+timedelta(days=14)).isoformat()
    year,month=divmod(start.year*12+start.month-1+MONTHS[period],12);month+=1
    return date(year,month,min(start.day,monthrange(year,month)[1])).isoformat()


class Provisioner(PeerStore):
    def create(self,data,request_id,template_path,now=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',request_id):
            raise StateError('A stable idempotency key is required')
        values=normalized(data)
        payload=hashlib.sha256(json.dumps(['create',values],sort_keys=True).encode()).hexdigest()
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None:raise StateError('Timezone-aware clock required')
        with self.locked():
            self._recover()
            with self.connect() as con:
                self._schema(con)
                old=con.execute('SELECT * FROM awg_operations WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old['payload_hash']!=payload:raise StateError('Idempotency key reused')
                    return json.loads(old['result'])
                con.execute('BEGIN IMMEDIATE')
                if getattr(self,'stage_limit',None) is not None:
                    deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
                    rows=con.execute('SELECT id FROM clients'+(' WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients)' if deleted else '')).fetchall()
                    if sum(str(row[0]) not in self.protected for row in rows)>=self.stage_limit:
                        raise StateError('Staging test client limit reached')
                owners=con.execute('SELECT DISTINCT user_id FROM clients').fetchall()
                configured_owner=getattr(self,'owner_id',None)
                if configured_owner is not None:
                    if configured_owner not in {row[0] for row in owners}:
                        raise StateError('Configured test owner is not present in the database')
                    owner=configured_owner
                else:
                    if len(owners)!=1:raise StateError('A single existing database owner is required')
                    owner=owners[0][0]
                referrer=None
                if values['referrer_id'] is not None:
                    if getattr(self,'cross_owner_referrals',False):
                        referrer=con.execute('SELECT * FROM clients WHERE id=?',(values['referrer_id'],)).fetchone()
                    else:
                        referrer=con.execute('SELECT * FROM clients WHERE id=? AND user_id=?',
                                             (values['referrer_id'],owner)).fetchone()
                    if referrer is None:raise StateError('Referrer not found')
                    deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
                    if deleted and con.execute('SELECT 1 FROM awg_deleted_clients WHERE client_id=?',(values['referrer_id'],)).fetchone():raise StateError('Referrer is archived')
                before=self.config.read_text()
                header,blocks=peer_blocks(before)
                # awg-quick permits repeated PostUp/PostDown lines. Preserve
                # those verbatim while rejecting ambiguous address/save flags.
                for field in ('Address','SaveConfig'):
                    if len(re.findall(r'(?m)^[ \t]*'+field+r'[ \t]*=',header))>1:
                        raise StateError('Ambiguous server interface fields')
                server=configparser.ConfigParser(interpolation=None,strict=False)
                server.optionxform=str
                # The full server config has duplicate Peer sections; parse Interface only.
                server.read_string(header)
                if server.getboolean('Interface','SaveConfig',fallback=False):
                    raise StateError('SaveConfig must be disabled before managed writes')
                try:interface=ipaddress.ip_interface(server['Interface']['Address'])
                except (KeyError,ValueError):raise StateError('One server address is required') from None
                if interface.version!=4:raise StateError('IPv4 provisioning only')
                live=self.runtime.peers()  # Unavailable interface cannot create files/rows.
                used=self.runtime.allowed_ips()
                for _,fields,_ in blocks:
                    used.extend(ipaddress.ip_network(n.strip(),strict=False)
                                for n in fields['AllowedIPs'].split(','))
                self.profiles.mkdir(mode=0o700,parents=True,exist_ok=True)
                for path in self.profiles.glob('*/metadata.json'):
                    meta=json.loads(path.read_text())
                    used.append(ipaddress.ip_network(str(meta['address']).split('/')[0]+'/32'))
                address=next((host for host in interface.network.hosts()
                              if host!=interface.ip and not any(host in net for net in used if net.version==4)),None)
                if address is None:raise StateError('No unused client address')
                template=configparser.ConfigParser(interpolation=None)
                template.optionxform=str
                with open(template_path) as source:template.read_file(source)
                if set(template.sections())!={'Interface','Peer'}:
                    raise StateError('Template must contain one Interface and one Peer')
                if template['Peer'].get('PublicKey')!=self.runtime.server_public_key():
                    raise StateError('Template server public key mismatch')
                cid=int(con.execute('SELECT coalesce(max(id),0)+1 FROM clients').fetchone()[0])
                numeric=[int(p.name) for p in self.profiles.iterdir() if p.is_dir() and p.name.isdigit()]
                cid=max([cid,*[n+1 for n in numeric]])
                if cid>9999999999:raise StateError('Client ID range exhausted')
                final=self.profiles/str(cid)
                if final.exists():raise StateError('Profile directory already exists')
                private,public,psk=self.runtime.new_keys()
                if public in live or any(key==public for key,_,_ in blocks):
                    raise StateError('Generated peer key is already in use')
                template['Interface']['PrivateKey']=private
                template['Interface']['Address']=str(address)+'/32'
                template['Peer']['PresharedKey']=psk
                output=io.StringIO();template.write(output)
                block='[Peer]\nPublicKey = '+public+'\nPresharedKey = '+psk+'\nAllowedIPs = '+str(address)+'/32\n'
                after=before.rstrip()+'\n\n'+block
                start=date.fromisoformat(values['start_date'])
                if values['period']=='trial':start=billing_date(now)
                end=deadline(start,values['period'],now)
                meta=dict(client_id=cid,name=values['name'],address=str(address),
                          public_key=public,period=values['period'],end_date=end,enabled=True)
                after_meta=json.dumps(meta,ensure_ascii=False,indent=2)
                related=[]
                bonus=BONUS.get(values['period']) if referrer is not None else None
                referral_end=None
                if bonus and referrer['period']!='lifetime':
                    refpath=self._metadata(referrer['id'])
                    previous=refpath.read_text()
                    refmeta=json.loads(previous)
                    if str(refmeta['client_id'])!=str(referrer['id']):
                        raise StateError('Referrer metadata mismatch')
                    referral_end=self._referral_end(referrer,bonus,billing_date(now),now)
                    refmeta['end_date']=referral_end
                    related.append(dict(cid=str(referrer['id']),before=previous,
                                        after=json.dumps(refmeta,ensure_ascii=False,indent=2)))
                backup=self.state/('op-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
                material=backup/'profile';material.mkdir(mode=0o700)
                atomic_write(material/'client.conf',output.getvalue().encode())
                atomic_write(material/'metadata.json',after_meta.encode())
                import sqlite3
                with sqlite3.connect(backup/'clients.db') as target:
                    with sqlite3.connect(self.db_path) as source:source.backup(target)
                os.chmod(backup/'clients.db',0o600)
                journal=dict(request_id=request_id,cid=str(cid),public_key=public,
                             before_block=None,after_block=block,before_config=before,
                             after_config=after,before_metadata=None,after_metadata=after_meta,
                             new_profile_backup=str(material),related_metadata=related)
                atomic_write(backup/'journal.json',json.dumps(journal).encode())
                atomic_write(self.state/'pending.json',json.dumps(journal).encode())
                self.fault('prepared')
                temporary=self.profiles/('.new-'+uuid.uuid4().hex)
                shutil.copytree(material,temporary)
                for path in temporary.iterdir():
                    with open(path,'rb') as handle:os.fsync(handle.fileno())
                sync_dir(temporary);os.replace(temporary,final);sync_dir(self.profiles)
                self.fault('profile_written')
                atomic_write(self.config,after.encode())
                self.fault('config_written')
                self.runtime.apply(public,block)
                self.fault('runtime_applied')
                for change in related:
                    atomic_write(self._metadata(change['cid']),change['after'].encode())
                self.fault('referral_metadata_written')
                con.execute('INSERT INTO clients(id,user_id,name,phone,start_date,end_date,period,note,referrer_id,wg_client_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (cid,owner,values['name'],values['phone'],start.isoformat(),end,
                             values['period'],values['note'],values['referrer_id'],str(cid)))
                con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',
                            (owner,cid,'created','Client and separate AWG profile created'))
                if referrer is not None and BONUS.get(values['period']):
                    self._referral(con,cid,referrer,BONUS[values['period']],referral_end)
                result=dict(ok=True,id=str(cid),client_id=cid,wg_client_id=str(cid),
                            address=str(address),enabled=True)
                con.execute('INSERT INTO awg_operations(request_id,payload_hash,result) VALUES(?,?,?)',
                            (request_id,payload,json.dumps(result)))
                con.commit();self.fault('db_committed')
                (self.state/'pending.json').rename(backup/'committed.json');sync_dir(self.state)
                return result

    @staticmethod
    def _referral_end(referrer,days,today,now=None):
        raw=referrer['end_date']
        if raw and len(raw)>10 and now is not None:
            old=subscription_deadline(referrer['period'],raw)
            return (max(now,old)+timedelta(days=days)).isoformat()
        try:base=max(today,date.fromisoformat(str(referrer['end_date'])[:10]))
        except (ValueError,TypeError):base=today
        return (base+timedelta(days=days)).isoformat()

    @staticmethod
    def _referral(con,cid,referrer,days,end):
        con.execute('CREATE TABLE IF NOT EXISTS referral_awards(referred_client_id INTEGER PRIMARY KEY,referrer_client_id INTEGER NOT NULL,bonus_days INTEGER NOT NULL,awarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        con.execute('INSERT INTO referral_awards(referred_client_id,referrer_client_id,bonus_days) VALUES(?,?,?)',
                    (cid,referrer['id'],days))
        if referrer['period']=='lifetime':return
        con.execute('UPDATE clients SET end_date=? WHERE id=?',(end,referrer['id']))
        con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',
                    (referrer['user_id'],referrer['id'],'referral_bonus','Referral bonus +'+str(days)+' days from client #'+str(cid)))
