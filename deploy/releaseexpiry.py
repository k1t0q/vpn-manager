"""Owner-approved removal of five migration expiry holds. Never restarts AWG/Xray."""
from datetime import datetime,timezone
import hashlib,json,os,sqlite3,subprocess,sys,tarfile,time
from pathlib import Path
from urllib.request import Request,urlopen
sys.path.insert(0,'/usr/local/libexec/vpn-manager-production')
from awg_persistence import subscription_deadline,AWGRuntime,peer_blocks
DB=Path('/opt/vpn-manager/data/clients-staging.db');ROOT=Path('/var/lib/vpn-manager-production')
PROFILES=Path('/opt/vpn-manager/awg-clients');CONFIG=Path('/etc/amnezia/amneziawg/awg-test.conf')
TARGETS={'36','37','38','45','47'}
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=40).stdout.strip()
def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 os.umask(0o077);os.environ['NO_PROXY']='127.0.0.1,localhost'
 if (ROOT/'operations/pending.json').exists():raise SystemExit('Pending operation; inspect before expiry')
 base=json.loads((ROOT/'baseline.json').read_text())
 if TARGETS!=set(base['initial_expiry_hold']):raise SystemExit('Migration hold set differs from reviewed five clients')
 runtime=AWGRuntime();before=runtime.peers();xray=run('systemctl','show','xray.service','-p','MainPID','--value')
 backup=Path('/root/vpn-manager-backups')/('approved-expiry-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
 backup.mkdir(mode=0o700)
 run('systemctl','stop','vpn-manager-live-bridge.service')
 try:
  with sqlite3.connect(backup/'clients.db') as destination:
   with sqlite3.connect(DB) as source:source.backup(destination)
  (backup/'awg-test.conf').write_bytes(CONFIG.read_bytes())
  with tarfile.open(backup/'profiles.tar.gz','w:gz') as archive:archive.add(PROFILES,arcname='profiles')
  with sqlite3.connect(DB) as con:
   con.row_factory=sqlite3.Row
   removed={str(r[0]) for r in con.execute('SELECT client_id FROM awg_deleted_clients')}
   rows={str(r['id']):dict(r) for r in con.execute('SELECT * FROM clients')}
   materials={cid:hashlib.sha256((PROFILES/cid/'client.conf').read_bytes()).hexdigest() for cid in rows if cid not in removed}
   metas={cid:(PROFILES/cid/'metadata.json').read_bytes() for cid in materials}
   targets={};now=datetime.now(timezone.utc)
   for cid in TARGETS:
    row=rows[cid];end=subscription_deadline(row['period'],row['end_date'])
    if cid in removed or end is None or end>now:continue
    if row['end_date']!=base['initial_expiry_hold'][cid]:continue
    meta=json.loads(metas[cid]);targets[cid]=meta['public_key']
   con.execute('BEGIN IMMEDIATE')
   con.execute('CREATE TABLE IF NOT EXISTS initial_expiry_hold_releases(client_id INTEGER PRIMARY KEY,released_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
   for cid in targets:
    result=con.execute('INSERT OR IGNORE INTO initial_expiry_hold_releases(client_id) VALUES(?)',(cid,))
    if result.rowcount:
     con.execute('INSERT INTO client_history(user_id,client_id,event_type,description) VALUES(?,?,?,?)',(rows[cid]['user_id'],cid,'expiry_hold_released','Administrator approved automatic expiry for migrated subscription'))
  (backup/'approval.json').write_text(json.dumps(dict(approved=True,client_ids=sorted(targets),operation='release_expiry_holds')))
 finally:run('systemctl','start','vpn-manager-live-bridge.service')
 env=dict(line.split('=',1) for line in Path('/etc/vpn-manager-live-bridge.env').read_text().splitlines() if '=' in line)
 for _ in range(40):
  try:
   req=Request('http://127.0.0.1:5055/api/managed/health',headers={'X-VPN-Token':env['PANEL_AWG_TOKEN']})
   with urlopen(req,timeout=3) as response:health=json.load(response)
   if health.get('scheduler_ok') and not(set(targets.values()) & runtime.peers()):break
  except OSError:pass
  time.sleep(1)
 else:raise SystemExit('Expiry verification incomplete; inspect private backup and active scheduler; no database was restored')
 after=runtime.peers();assert after==before-set(targets.values())
 assert all(hashlib.sha256((PROFILES/cid/'client.conf').read_bytes()).hexdigest()==digest for cid,digest in materials.items())
 assert all((PROFILES/cid/'metadata.json').read_bytes()==data for cid,data in metas.items() if cid not in targets)
 for cid in targets:
  meta=json.loads((PROFILES/cid/'metadata.json').read_text());assert meta['enabled'] is False and meta['auto_disabled'] is True
 assert xray==run('systemctl','show','xray.service','-p','MainPID','--value')
 result=dict(ok=True,disabled_client_ids=sorted(targets),other_runtime_peers_unchanged=True,all_config_files_unchanged=True,other_metadata_unchanged=True,auto_restore_after_renewal=True,backup=str(backup))
 Path('/root/vpn-expiry-release-result.json').write_text(json.dumps(result,indent=2))
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
