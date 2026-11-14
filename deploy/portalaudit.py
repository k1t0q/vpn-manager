from pathlib import Path
import sqlite3,json,subprocess
p=Path('/opt/vpn-manager/awg-clients')
items=[]
for d in p.iterdir():
 if d.name.isdigit() and (d/'metadata.json').exists():
  m=json.loads((d/'metadata.json').read_text())
  items.append(dict(id=int(d.name),keys=sorted(m),number_fields={k:v for k,v in m.items() if k in ('profile_name','profile_number','label','display_name')},name_has_bot='BOT #' in str(m.get('name',''))))
print('META_SHAPE',json.dumps(items[:1]))
with sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True) as db:
 print('SCHEMAS',json.dumps([r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE type='table'") if any(w in str(r[0]) for w in ('label','profile_name','profile_number','registry'))]))
print('BRIDGE_EXEC',subprocess.run(['systemctl','show','vpn-manager-live-bridge.service','-p','ExecStart','--value'],capture_output=True,text=True).stdout.strip())
