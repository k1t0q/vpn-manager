"""Owner-authorized production smoke test; changes disposable new clients only."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
from urllib.request import Request,urlopen

ROOT=Path('/var/lib/vpn-manager-production')
DB=Path('/opt/vpn-manager/data/clients-staging.db')
PROFILES=Path('/opt/vpn-manager/awg-clients')
BASE='http://127.0.0.1:5056'
def main():
    os.umask(0o077)
    original=json.loads((ROOT/'baseline.json').read_text())
    ids=set(original['clients'])
    with sqlite3.connect(DB) as con:
        con.row_factory=sqlite3.Row
        before={str(r['id']):dict(r) for r in con.execute('SELECT * FROM clients') if str(r['id']) in ids}
    files={cid:hashlib.sha256((PROFILES/cid/'client.conf').read_bytes()).hexdigest() for cid in ids}
    xray=subprocess.run(['systemctl','show','xray.service','-p','MainPID','--value'],capture_output=True,text=True,check=True).stdout
    env=dict(line.split('=',1) for line in Path('/etc/vpn-manager-live.env').read_text().splitlines() if '=' in line)
    token='';passed=[];created=[];stage='login';nonce=secrets.token_hex(12)
    def api(method,path,data=None,key=None,raw=False):
        headers={'Origin':'https://185-23-239-142.sslip.io:8444'}
        if token:headers['Authorization']='Bearer '+token
        if data is not None:headers['Content-Type']='application/json'
        if method!='GET':headers['Idempotency-Key']=key or ('verify-'+nonce+'-'+secrets.token_hex(8))
        req=Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
        with urlopen(req,timeout=35) as response:body=response.read()
        return body if raw else json.loads(body)
    def row(cid):
        with sqlite3.connect(DB) as con:
            con.row_factory=sqlite3.Row
            return dict(con.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone())
    def create(period,ref=None):
        name='LIVE VERIFICATION '+nonce+' '+str(len(created))
        payload=dict(name=name,period=period,start_date=datetime.now(timezone(timedelta(hours=3))).date().isoformat(),phone='',note='Disposable verification profile')
        if ref is not None:payload['referrer_id']=int(ref)
        key='verify-create-'+nonce+'-'+secrets.token_hex(8)
        result=api('POST','/api/clients/provision',payload,key)
        cid=str(result['id']);assert cid not in ids
        created.append(cid)
        assert api('POST','/api/clients/provision',payload,key)['id']==result['id']
        return cid
    try:
        token=api('POST','/auth/login',dict(code=env['PANEL_ADMIN_CODE']))['access_token']
        assert len(api('GET','/api/clients')['clients'])==59
        passed.append('login_and_59_clients')
        stage='trial_and_create_idempotency';ref=create('trial')
        trial=datetime.fromisoformat(row(ref)['end_date'])
        assert abs((trial-datetime.now(timezone.utc)).total_seconds()-86400)<30
        passed.extend(['trial_exact_24h','atomic_creation_and_retry'])
        stage='qr_and_config'
        material=api('GET','/api/wg/client/'+ref+'/config',raw=True)
        assert b'PrivateKey' in material and b'Endpoint' in material and b'51840' in material
        assert b'<svg' in api('GET','/api/wg/client/'+ref+'/qr',raw=True)
        passed.append('config_and_svg_qr')
        stage='toggle'
        api('POST','/api/wg/client/'+ref+'/disable',{})
        assert json.loads((PROFILES/ref/'metadata.json').read_text())['enabled'] is False
        api('POST','/api/clients/'+ref+'/extend',dict(period='1m'))
        assert json.loads((PROFILES/ref/'metadata.json').read_text())['enabled'] is False
        api('POST','/api/wg/client/'+ref+'/enable',{})
        assert json.loads((PROFILES/ref/'metadata.json').read_text())['enabled'] is True
        passed.append('manual_disable_renew_preserves_disable_and_enable')
        stage='delivery_and_edit'
        api('PATCH','/api/clients/'+ref,dict(name='LIVE VERIFICATION '+nonce+' edited',phone='test',note='test'))
        assert api('POST','/api/clients/'+ref+'/delivery',dict(delivered=True))['delivered_at']
        assert api('POST','/api/clients/'+ref+'/delivery',dict(delivered=False))['delivered_at'] is None
        passed.append('edit_and_delivery_mark_reset')
        stage='referral_bonuses'
        for period,days in [('1m',14),('6m',30),('12m',60)]:
            old=datetime.fromisoformat(row(ref)['end_date'])
            child=create(period,ref)
            current=datetime.fromisoformat(row(ref)['end_date'])
            assert (current-old).days==days
            with sqlite3.connect(DB) as con:
                assert con.execute('SELECT bonus_days FROM referral_awards WHERE referred_client_id=?',(child,)).fetchone()[0]==days
            api('POST','/api/clients/'+child+'/delete',{})
            created.remove(child)
        passed.append('referral_14_30_60_days')
        stage='expiry_restore'
        api('PATCH','/api/clients/'+ref,dict(period='1m',end_date='2020-01-01'))
        assert json.loads((PROFILES/ref/'metadata.json').read_text())['enabled'] is False
        assert api('POST','/api/clients/'+ref+'/extend',dict(period='6m'))['enabled'] is True
        api('POST','/api/clients/'+ref+'/extend',dict(period='12m'))
        passed.append('expiry_disable_renew_auto_restore')
        stage='lifetime'
        assert api('POST','/api/clients/'+ref+'/extend',dict(period='lifetime'))['end_date'] is None
        assert api('POST','/api/clients/'+ref+'/extend',dict(period='1m'))['end_date'] is None
        passed.append('lifetime_stays_unlimited_after_renew')
        stage='history'
        assert len(api('GET','/api/clients/'+ref+'/history')['history'])>=10
        assert len(api('GET','/api/clients/import')['candidates'])==0
        passed.append('history_and_empty_safe_import')
        stage='complete'
    except Exception as error:
        passed.append('FAILED_AT_'+stage+'_'+type(error).__name__)
    finally:
        for cid in list(created):
            assert cid not in ids and row(cid)['name'].startswith('LIVE VERIFICATION '+nonce)
            api('POST','/api/clients/'+cid+'/delete',{})
        if token:api('POST','/auth/logout',{})
    with sqlite3.connect(DB) as con:
        con.row_factory=sqlite3.Row
        after={str(r['id']):dict(r) for r in con.execute('SELECT * FROM clients') if str(r['id']) in ids}
        integrity=con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        archived={str(r[0]) for r in con.execute('SELECT client_id FROM awg_deleted_clients')}
    live=set(subprocess.run(['awg','show','awg-test','peers'],capture_output=True,text=True,check=True).stdout.split())
    unchanged=before==after and all(hashlib.sha256((PROFILES/cid/'client.conf').read_bytes()).hexdigest()==digest for cid,digest in files.items())
    report=dict(ok=stage=='complete' and unchanged and integrity,checks=passed,original_clients_unchanged=unchanged,
        original_runtime_peers_preserved=set(original['runtime_peers']).issubset(live),runtime_peers=len(live),
        original_clients=59,integrity_ok=integrity,originals_not_archived=not(ids & archived),
        no_pending_journal=not(ROOT/'operations/pending.json').exists(),
        xray_pid_unchanged=xray==subprocess.run(['systemctl','show','xray.service','-p','MainPID','--value'],capture_output=True,text=True,check=True).stdout)
    Path('/root/vpn-live-verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
