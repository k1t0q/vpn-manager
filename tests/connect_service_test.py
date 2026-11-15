import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from connect_service import create_app, ORIGIN


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name);self.state=base/'state';self.material=base/'material'
        self.state.mkdir();self.material.mkdir()
        self.contents=b'[Interface]\nPrivateKey = private-fixture-only\n[Peer]\nPublicKey = public-fixture\n'
        (self.material/'client.conf').write_bytes(self.contents)
        self.snapshot=dict(client_id=900,available=True,updated_at=time.time(),sha256=hashlib.sha256(self.contents).hexdigest())
        self.save_snapshot()
        self.app=create_app(dict(STATE=str(self.state),MATERIAL=str(self.material),ADMIN_TOKEN='a'*40,TESTING=True))
        self.client=self.app.test_client();self.admin={'X-Connect-Admin':'a'*40}
        self.number=0

    def save_snapshot(self):
        (self.material/'status.json').write_text(json.dumps(self.snapshot))

    def issue(self,action='issue',key=None,cid=900):
        self.number+=1
        return self.client.post('/internal/link',json={'client_id':cid,'action':action},headers=dict(self.admin,**{'Idempotency-Key':key or 'test-request-number-'+str(self.number)}))

    def parts(self,url):
        parts=urlsplit(url);return {'handle':parts.path[1:],'token':parts.fragment}

    def download(self,parts):
        return self.client.post('/api/download',json=parts,headers={'Origin':ORIGIN})

    def test_admin_scope_and_stable_retry(self):
        self.assertEqual(self.client.get('/internal/status').status_code,401)
        self.assertEqual(self.issue(cid=901).status_code,403)
        first=self.issue(key='stable-retry-key-0001').json
        self.assertEqual(first,self.issue(key='stable-retry-key-0001').json)
        self.assertEqual(first['url'],self.issue().json['url'])
        self.assertEqual(self.issue('rotate',key='stable-retry-key-0001').status_code,409)

    def test_download_unchanged_and_secret_only_in_fragment(self):
        url=self.issue().json['url'];parts=self.parts(url)
        self.assertEqual(len(parts['token']),43);self.assertEqual(len(parts['handle']),22)
        response=self.download(parts)
        self.assertEqual(response.status_code,200);self.assertEqual(response.data,self.contents)
        landing=self.client.get('/'+parts['handle'])
        self.assertNotIn(parts['token'].encode(),landing.data)
        self.assertNotIn(b'private-fixture-only',landing.data)
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertIn('noindex',response.headers['X-Robots-Tag'])
        self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')
        with sqlite3.connect(self.state/'links.db') as con:
            raw=con.execute('SELECT secret FROM links').fetchone()[0]
        self.assertNotIn(parts['token'].encode(),raw)

    def test_revocation_rotation_and_persistence(self):
        first=self.parts(self.issue().json['url'])
        self.assertEqual(self.issue('revoke').status_code,200)
        self.assertEqual(self.download(first).status_code,404)
        self.assertEqual(self.issue().status_code,409)
        second=self.parts(self.issue('rotate').json['url'])
        self.assertNotEqual(first,second);self.assertEqual(self.download(first).status_code,404)
        self.assertEqual(self.download(second).status_code,200)
        restarted=create_app(dict(STATE=str(self.state),MATERIAL=str(self.material),ADMIN_TOKEN='a'*40,TESTING=True)).test_client()
        self.assertEqual(restarted.post('/api/download',json=second,headers={'Origin':ORIGIN}).data,self.contents)

    def test_disabled_changed_and_stale_material_fail_closed(self):
        parts=self.parts(self.issue().json['url'])
        self.snapshot['available']=False;self.save_snapshot()
        self.assertEqual(self.download(parts).status_code,404)
        self.snapshot['available']=True;self.save_snapshot()
        self.assertEqual(self.download(parts).status_code,200)
        (self.material/'client.conf').write_bytes(b'corrupt')
        self.assertEqual(self.download(parts).status_code,503)
        self.snapshot['updated_at']=time.time()-151;self.save_snapshot()
        self.assertEqual(self.download(parts).status_code,503)

    def test_unknown_and_revoked_same_error_and_no_path_input(self):
        parts=self.parts(self.issue().json['url'])
        unknown=self.download(dict(parts,token='b'*43))
        self.issue('revoke');revoked=self.download(parts)
        self.assertEqual(unknown.status_code,revoked.status_code);self.assertEqual(unknown.json,revoked.json)
        self.assertEqual(self.download(dict(handle='../../etc/passwd',token='b'*43)).status_code,404)
        self.assertEqual(self.client.post('/api/download',json=parts,headers={'Origin':'https://evil.test'}).status_code,403)

    def test_renewal_keeps_link_and_other_clients_not_exposed(self):
        first=self.issue().json['url']
        self.snapshot['end_date']='2030-01-01';self.save_snapshot()
        self.assertEqual(first,self.issue().json['url'])
        self.snapshot['client_id']=901;self.save_snapshot()
        self.assertEqual(self.download(self.parts(first)).status_code,404)


if __name__=='__main__':unittest.main()
