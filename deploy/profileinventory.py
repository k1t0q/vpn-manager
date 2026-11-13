"""Read-only inventory; no credential/profile content is printed."""
import json,sqlite3
from pathlib import Path
base=json.loads(Path('/var/lib/vpn-manager-production/baseline.json').read_text())
profiles=Path('/opt/vpn-manager/awg-clients')
with sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True) as con:
    rows=con.execute('SELECT id FROM clients').fetchall()
    exists=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
    deleted={str(r[0]) for r in con.execute('SELECT client_id FROM awg_deleted_clients')} if exists else set()
ids={str(r[0]) for r in rows}
active=ids-deleted
missing={cid:[name for name in ('metadata.json','client.conf') if not (profiles/cid/name).exists()] for cid in active}
missing={cid:names for cid,names in missing.items() if names}
print(json.dumps(dict(active_clients=len(active),archived_clients=len(deleted),original_clients_active=len(set(base['clients']) & active),original_clients_archived=sorted(set(base['clients']) & deleted),missing_active_files=missing)))
