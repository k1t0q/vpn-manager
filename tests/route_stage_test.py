import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('route_stage',Path(__file__).resolve().parents[1]/'deploy/route_stage.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class RoutesTests(unittest.TestCase):
 def test_only_8443_site_changes(self):
  production='example.test:443 {\n reverse_proxy 127.0.0.1:4430\n}\n'
  original=production+'https://example.test:8443 {\n handle /api/* {\n reverse_proxy 127.0.0.1:5052\n }\n}\n'
  result=module.candidate(original)
  self.assertTrue(result.startswith(production))
  self.assertEqual(result.replace(module.IMPORT+'\n',''),original)
  self.assertEqual(module.candidate(result),result)
 def test_ambiguous_or_missing_site_fails_without_change(self):
  for text in ['example.test:443 {\n}\n','a:8443 {\n}\nb:8443 {\n}\n']:
   with self.assertRaises(RuntimeError):module.candidate(text)
if __name__=='__main__':unittest.main()
