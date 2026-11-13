"""Check production code-file consistency and login without printing credentials."""
import hmac,json,os
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
def main():
    os.environ['NO_PROXY']='127.0.0.1,localhost,185-23-239-142.sslip.io'
    code=Path('/root/vpn-manager-live-login-code').read_text().strip()
    env=dict(line.split('=',1) for line in Path('/etc/vpn-manager-live.env').read_text().splitlines() if '=' in line)
    report={'code_file_matches_service_env':hmac.compare_digest(code,env['PANEL_ADMIN_CODE'])}
    base='https://185-23-239-142.sslip.io:8444/live-api'
    headers={'Content-Type':'application/json','Origin':'https://185-23-239-142.sslip.io:8444'}
    request=Request(base+'/auth/login',data=json.dumps({'code':code}).encode(),headers=headers,method='POST')
    try:
        with urlopen(request,timeout=20) as response:
            result=json.load(response);report['https_login_status']=response.status
        token=result.get('access_token','')
        report['session_issued']=bool(token)
        if token:
            request=Request(base+'/auth/logout',data=b'{}',headers={**headers,'Authorization':'Bearer '+token},method='POST')
            with urlopen(request,timeout=10) as response:report['test_session_removed']=response.status==200
    except HTTPError as error:report['https_login_status']=error.code
    print(json.dumps(report))
if __name__=='__main__':main()
