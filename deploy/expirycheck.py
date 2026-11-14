"""Read-only expiry audit; never prints names, keys or profile configuration."""
from datetime import datetime,timezone
import json,sqlite3,sys
from pathlib import Path
sys.path.insert(0,'/usr/local/libexec/vpn-manager-production')
from awg_persistence import subscription_deadline,AWGRuntime
DB=Path('/opt/vpn-manager/data/clients-staging.db')
BASE=Path('/var/lib/vpn-manager-production/baseline.json')
now=datetime.now(timezone.utc);base=json.loads(BASE.read_text());keys=AWGRuntime().peers()
with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as con:
 con.row_factory=sqlite3.Row
 deleted=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
 removed={str(r[0]) for r in con.execute('SELECT client_id FROM awg_deleted_clients')} if deleted else set()
 exists=con.execute("SELECT 1 FROM sqlite_master WHERE name='initial_expiry_hold_releases'").fetchone()
 released={str(r[0]) for r in con.execute('SELECT client_id FROM initial_expiry_hold_releases')} if exists else set()
 report=[]
 for row in con.execute('SELECT * FROM clients'):
  cid=str(row['id'])
  if cid in removed:continue
  end=subscription_deadline(row['period'],row['end_date'])
  if end is not None and end<=now:
   meta=json.loads((Path('/opt/vpn-manager/awg-clients')/cid/'metadata.json').read_text())
   report.append(dict(id=cid,end_date=row['end_date'],currently_enabled=meta['public_key'] in keys,held=cid in base.get('initial_expiry_hold',{}) and cid not in released and base['initial_expiry_hold'][cid]==row['end_date']))
 print(json.dumps(dict(checked_at=now.isoformat(),expired_clients=report,runtime_peer_count=len(keys)),indent=2))
