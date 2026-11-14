"""Verify profile labels and direct downloads without exposing any VPN material."""
import hashlib,json,os,re,secrets,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
DB=Path('/opt/vpn-manager/data/clients-staging.db')
SITE=Path('/opt/vpn-manager-production/public')
PROFILES=Path('/opt/vpn-manager/awg-clients')
def main():
    os.umask(0o077);os.environ['NO_PROXY']='127.0.0.1,localhost'
    env=dict(line.split('=',1) for line in Path('/etc/vpn-manager-live.env').read_text().splitlines() if '=' in line)
    token='';nonce=secrets.token_hex(12);new=None
    def api(method,path,data=None,key=None,raw=False):
        headers={'Origin':'https://185-23-239-142.sslip.io:8444'}
        if token:headers['Authorization']='Bearer '+token
        if data is not None:headers['Content-Type']='application/json'
        if method!='GET':headers['Idempotency-Key']=key or 'profile-label-check-'+nonce+'-'+secrets.token_hex(8)
        req=Request('http://127.0.0.1:5056'+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers=headers)
        with urlopen(req,timeout=30) as response:body=response.read()
        return body if raw else json.loads(body)
    report={}
    token=api('POST','/auth/login',{'code':env['PANEL_ADMIN_CODE']})['access_token']
    try:
        clients=api('GET','/api/clients')['clients']
        peers=api('GET','/api/wg/clients')['clients']
        assert len(clients)==len(peers)
        labels=[peer['profile_name'] for peer in peers]
        assert len(set(labels))==len(labels) and all(re.fullmatch(r'CAESAR BOT #[1-9][0-9]*',label) for label in labels)
        before={}
        for peer in peers:
            cid=str(peer['client_id']);path=PROFILES/cid/'client.conf'
            material=api('GET','/api/wg/client/'+str(peer['id'])+'/config',raw=True)
            assert material==path.read_bytes()
            before[cid]=hashlib.sha256(material).hexdigest()
        report.update(active_clients=len(clients),unique_stable_labels=True,existing_config_api_verified=len(peers))
        payload={'name':'PROFILE LABEL TEST '+nonce,'phone':'','note':'Disposable naming verification','period':'lifetime','start_date':'2026-11-13'}
        request_id='profile-create-'+nonce
        result=api('POST','/api/clients/provision',payload,request_id)
        new=str(result['id']);label=result['profile_name']
        assert api('POST','/api/clients/provision',payload,request_id)['id']==result['id']
        original=api('GET','/api/wg/client/'+new+'/config',raw=True)
        api('PATCH','/api/clients/'+new,{'name':'PROFILE LABEL TEST '+nonce+' renamed'})
        peer=next(item for item in api('GET','/api/wg/clients')['clients'] if str(item['client_id'])==new)
        assert peer['profile_name']==label
        assert api('GET','/api/wg/client/'+new+'/config',raw=True)==original
        report.update(new_profile_stable_number=True,rename_keeps_profile_name_and_config=True,creation_retry_does_not_duplicate=True)
    finally:
        if new:
            api('POST','/api/clients/'+new+'/delete',{})
        if token:api('POST','/auth/logout',{})
    assert all(hashlib.sha256((PROFILES/cid/'client.conf').read_bytes()).hexdigest()==digest for cid,digest in before.items())
    report['existing_config_bytes_unchanged']=True
    report['test_profile_removed']=True
    source=(SITE/'sqlite-manager.js').read_text()
    start=source.index(" const peerName=peerLabel(c)||'AmneziaWG';")
    end=source.index(" }else if(btn.dataset.act==='wg-create')",start)
    block=source[start:end]
    assert 'navigator.share' not in block and 'link.download=fileName' in block
    fixture='<!doctype html><meta charset="utf-8"><title>Configuration download check</title><h1>Проверка скачивания</h1><p>Тестовый файл без ключей и настроек VPN.</p><button id="test">Скачать тестовый .conf</button><p id="status"></p><script>const c={id:999};const peerLabel=()=>"CAESAR BOT #999";const msg=text=>document.getElementById("status").textContent=text;const blob=new Blob(["# Download check only. No VPN keys or settings.\\n"],{type:"application/octet-stream"});document.getElementById("test").onclick=()=>{'+block+'};</script>'
    target=SITE/'download-check.html';target.write_text(fixture);target.chmod(0o644)
    Path('/root/vpn-profile-label-verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
