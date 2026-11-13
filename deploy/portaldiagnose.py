from pathlib import Path
import json,sqlite3
material=json.loads(Path('/var/lib/vpn-manager-connect/material/portal.json').read_text())
with sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row
 rows=db.execute('SELECT id,wg_client_id FROM clients WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients) AND wg_client_id IS NOT NULL').fetchall()
 labels={str(r[0]):r[1] for r in db.execute('SELECT client_id,label_number FROM awg_profile_labels')}
expected={str(r['id']) for r in rows};actual=set(material['clients'])
print('COUNTS',len(expected),len(actual),'MISSING',sorted(expected-actual),'EXTRA',sorted(actual-expected))
for row in rows:
 cid=str(row['id'])
 if cid in actual:continue
 peer=str(row['wg_client_id']);folder=Path('/opt/vpn-manager/awg-clients')/cid;meta=folder/'metadata.json'
 data=json.loads(meta.read_text()) if meta.exists() else {}
 print(json.dumps(dict(id=row['id'],link_numeric=peer.isdigit(),link_equals_id=peer==cid,label_present=cid in labels,metadata_id=data.get('client_id'),profile_exists=(folder/'client.conf').is_file(),folder_symlink=folder.is_symlink())))
