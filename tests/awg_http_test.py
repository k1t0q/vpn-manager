import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import sys
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
spec=importlib.util.spec_from_file_location('awg_http',Path(__file__).resolve().parents[1]/'deploy/awg_test_server.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class HTTPBoundaryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.bridge=SimpleNamespace(store=SimpleNamespace(state=Path(self.tmp.name)),dispatch=Mock(return_value=({'ok':True,'id':'60'},201,'application/json')))
  self.server=module.HTTPServer(('127.0.0.1',0),module.Handler)
  self.server.token='synthetic-token-for-test-only';self.server.bridge=self.bridge
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.addCleanup(self.close)
 def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
 def request(self,headers=None,data=b'{}'):
  return urlopen(Request('http://127.0.0.1:'+str(self.server.server_port)+'/api/managed/create',headers=headers or {},data=data),timeout=2)
 def test_missing_auth_never_dispatches(self):
  with self.assertRaises(HTTPError) as error:self.request()
  self.assertEqual(error.exception.code,401);self.bridge.dispatch.assert_not_called()
 def test_authenticated_request_preserves_idempotency_key(self):
  with self.request({'X-VPN-Token':self.server.token,'Idempotency-Key':'http-create-test-01'}) as response:
   self.assertEqual(response.status,201);self.assertEqual(json.load(response),{'ok':True,'id':'60'})
  self.bridge.dispatch.assert_called_once_with('POST','/api/managed/create',{},'http-create-test-01')
 def test_oversize_request_never_dispatches(self):
  with self.assertRaises(HTTPError) as error:self.request({'X-VPN-Token':self.server.token},b'x'*16385)
  self.assertEqual(error.exception.code,413);self.bridge.dispatch.assert_not_called()


if __name__=='__main__':unittest.main()
