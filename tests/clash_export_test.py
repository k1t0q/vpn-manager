import base64,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from clash_export import convert
KEY=base64.b64encode(bytes(range(32))).decode()
CONF=('''[Interface]
PrivateKey = KEY
Address = 10.10.0.93/32
DNS = 1.1.1.1, 8.8.8.8
MTU = 1280
Jc = 4
Jmin = 40
Jmax = 70
S1 = 16
S2 = 30
H1 = 123
H2 = 234
H3 = 345
H4 = 456
[Peer]
PublicKey = KEY
PresharedKey = KEY
Endpoint = 185.23.239.142:51840
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
''').replace('KEY',KEY).encode()
def parsed(contents=CONF):
 return {name:json.loads(value) for name,value in (line.split(': ',1) for line in convert(contents,'CAESAR VPN #93').decode().splitlines())}
class ClashExportTests(unittest.TestCase):
 def test_preserves_keys_ip_endpoint_and_obfuscation(self):
  cfg=parsed();p=cfg['proxies'][0]
  self.assertEqual(p['private-key'],KEY);self.assertEqual(p['public-key'],KEY);self.assertEqual(p['pre-shared-key'],KEY)
  self.assertEqual(p['ip'],'10.10.0.93');self.assertEqual(p['port'],51840);self.assertEqual(p['amnezia-wg-option']['h4'],456)
  self.assertEqual(p['persistent-keepalive'],25);self.assertEqual(cfg['rules'],['MATCH,VPN']);self.assertFalse(cfg['allow-lan']);self.assertEqual(cfg['log-level'],'silent')
 def test_unsupported_options_hooks_sections_routes_and_bad_keys_rejected(self):
  for changed in [CONF.replace(b'MTU =',b'PostUp ='),CONF+b'\n[Other]\nX=1',CONF.replace(b'0.0.0.0/0',b'10.0.0.0/8'),CONF.replace(KEY.encode(),b'invalid'),CONF.replace(b'Jmax = 70',b'Jmax = 3'),CONF+b'\nEndpoint = duplicate:22',CONF.replace(b'[Interface]',b'[DEFAULT]\nx=1\n[Interface]')]:
   with self.subTest(profile='invalid'):
    with self.assertRaises(Exception):convert(changed,'test')
 def test_v2_ranges_strings_and_ipv6_preserved(self):
  cfg=parsed(CONF.replace(b'H1 = 123',b'H1 = 123-456\nI1 = <b 0xab><r 10>\nS3 = 4\nS4 = 8').replace(b'10.10.0.93/32',b'10.10.0.93/32, fd00::93/128').replace(b'0.0.0.0/0',b'0.0.0.0/0, ::/0'))
  p=cfg['proxies'][0];self.assertEqual(p['ipv6'],'fd00::93');self.assertEqual(p['amnezia-wg-option']['h1'],'123-456');self.assertEqual(p['amnezia-wg-option']['i1'],'<b 0xab><r 10>')
if __name__=='__main__':unittest.main()
