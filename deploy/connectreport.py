from pathlib import Path
import json
d=json.loads(Path('/root/vpn-connect-audit.json').read_text())
for key in ['hostname','addresses','dns','public_dns','https','clients','caddy','caddy_routes']:
 print(key.upper()+': '+json.dumps(d[key],ensure_ascii=False))
for key,values in d['services'].items():print(key+': '+' '.join(values))
print('LISTENERS: '+'; '.join(d['listeners']))
