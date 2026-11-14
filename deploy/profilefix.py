"""Owner-requested profile labels and configuration download fix; no VPN restart."""
from datetime import datetime,timezone
import hashlib,json,os,re,secrets,shutil,sqlite3,subprocess,tempfile,time
from pathlib import Path
from urllib.request import Request,urlopen
CODE=Path('/usr/local/libexec/vpn-manager-production')
SITE=Path('/opt/vpn-manager-production/public')
STATE=Path('/var/lib/vpn-manager-production')
DB=Path('/opt/vpn-manager/data/clients-staging.db')
PROFILES=Path('/opt/vpn-manager/awg-clients')
RELEASE='6e0ec7b00e9901c124d8948ed01557cf4a691914'
FILES=[
('server/awg_provision.py',CODE/'awg_provision.py','943168f825b5dff81c5759b18bae7db417ccf74d33ad2d7789b49e0a665d0a63','95a8c399597049468f84990980d7f8471b20edc57e437eca34b44d2e480c6c77',0o600),
('deploy/awg_readonly_bridge.py',CODE/'awg_readonly_bridge.py','96b976a2f7d632c4517a97966d4cc860b2df0f42faba6732f221be9cd98c2f3f','bb8d846e0dfdd1b83347e8a60c083d5b1cf01e036de6fe2af19193af4ad4fd58',0o600),
('sqlite-manager.js',SITE/'sqlite-manager.js','881489a6eb5745958b6d13714faac06694d885715e9bff096c896280efe56dbe','734b2a310d9a42ccef4d1a5365d1177c6bea827795496f9bb1247dd89db79d83',0o644),
('index.html',SITE/'index.html','2e20ec581fceae83f6ad93e058962654e8020bb1054db1e19a9dad1fb9b4a5f4','3ba86165c5284073bbbd51fe8a00a22d3545f4039e27838fd563ba96c86f5273',0o644)]
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=40).stdout.strip()
def atomic(path,data,mode):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.profile-fix-')
    with os.fdopen(fd,'wb') as out:os.fchmod(out.fileno(),mode);out.write(data);out.flush();os.fsync(out.fileno())
    os.replace(tmp,path)
def main():
    if os.geteuid()!=0:raise SystemExit('Root required')
    os.umask(0o077);os.environ['NO_PROXY']='127.0.0.1,localhost'
    if not json.loads((STATE/'activation.json').read_text()).get('approved'):raise SystemExit('No approved production activation')
    if (STATE/'operations/pending.json').exists():raise SystemExit('Pending operation; review required')
    original=json.loads((STATE/'baseline.json').read_text());ids=set(original['clients'])
    peer_keys=run('awg','show','awg-test','peers')
    xray=run('systemctl','show','xray.service','-p','MainPID','--value')
    with sqlite3.connect(DB) as con:
        con.row_factory=sqlite3.Row
        original_rows={str(row['id']):dict(row) for row in con.execute('SELECT * FROM clients') if str(row['id']) in ids}
        table=con.execute("SELECT 1 FROM sqlite_master WHERE name='awg_deleted_clients'").fetchone()
        archived={str(row[0]) for row in con.execute('SELECT client_id FROM awg_deleted_clients')} if table else set()
    active_originals=ids-archived
    profile_hashes={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for cid in active_originals for path in (PROFILES/cid/'client.conf',PROFILES/cid/'metadata.json')}
    materials={}
    for source,target,old,new,mode in FILES:
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=old:raise SystemExit('Installed file differs from reviewed release')
        with urlopen('https://raw.githubusercontent.com/k1t0q/vpn-manager/'+RELEASE+'/'+source,timeout=30) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=new:raise SystemExit('Downloaded file digest mismatch')
        if source.endswith('.py'):compile(data,source,'exec')
        materials[source]=data
    backup=Path('/root/vpn-manager-backups')/('profile-labels-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    for source,target,old,new,mode in FILES:shutil.copy2(target,backup/target.name)
    run('systemctl','stop','vpn-manager-live-bridge.service')
    try:
        with sqlite3.connect(backup/'clients.db') as dst:
            with sqlite3.connect(DB) as src:src.backup(dst)
        with sqlite3.connect(DB) as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS awg_profile_labels(client_id INTEGER PRIMARY KEY,label_number INTEGER NOT NULL UNIQUE)')
            assigned={str(cid):number for cid,number in con.execute('SELECT client_id,label_number FROM awg_profile_labels')}
            used=set(assigned.values());rows=con.execute('SELECT id,name FROM clients ORDER BY id').fetchall()
            proposed={}
            for cid,name in rows:
                if str(cid) in assigned:continue
                meta_path=PROFILES/str(cid)/'metadata.json'
                meta=json.loads(meta_path.read_text()) if meta_path.exists() else {}
                raw=meta.get('profile_name') or meta.get('name') or name
                match=re.match(r'^(?:CAESAR|CEASER)\s+BOT\s*#\s*([1-9][0-9]*)(?=\s|$)',str(raw).strip(),re.I)
                if match:
                    number=int(match[1])
                    if number in used:raise RuntimeError('Duplicate historic profile number; review required')
                    proposed[str(cid)]=number;used.add(number)
            for cid,name in rows:
                if str(cid) in assigned:continue
                number=proposed.get(str(cid))
                if number is None:
                    number=int(cid)
                    if number in used:number=max(used|{0})+1
                    used.add(number)
                con.execute('INSERT INTO awg_profile_labels VALUES(?,?)',(cid,number))
        for source,target,old,new,mode in FILES:atomic(target,materials[source],mode)
        run('systemctl','start','vpn-manager-live-bridge.service')
        for attempt in range(30):
            try:
                with urlopen('http://127.0.0.1:5056/health',timeout=3) as response:health=json.load(response)
                if health.get('scheduler_ok'):break
            except OSError:pass
            time.sleep(1)
        else:raise RuntimeError('Patched service health failed')
    except Exception:
        run('systemctl','stop','vpn-manager-live-bridge.service')
        for source,target,old,new,mode in FILES:atomic(target,(backup/target.name).read_bytes(),mode)
        run('systemctl','start','vpn-manager-live-bridge.service')
        raise SystemExit('Code rolled back; label registry and current VPN state retained; inspect private backup')
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in profile_hashes.items())
    with sqlite3.connect(DB) as con:
        con.row_factory=sqlite3.Row
        after={str(row['id']):dict(row) for row in con.execute('SELECT * FROM clients') if str(row['id']) in ids}
        assert archived=={str(row[0]) for row in con.execute('SELECT client_id FROM awg_deleted_clients')}
    assert after==original_rows
    assert peer_keys==run('awg','show','awg-test','peers')
    assert xray==run('systemctl','show','xray.service','-p','MainPID','--value')
    (STATE/'profile-labels-release.json').write_text(json.dumps(dict(release=RELEASE,backup=str(backup),original_clients=59)))
    print('PROFILE_LABELS_AND_DOWNLOAD_READY; original records, active profile metadata/config bytes, archive state, runtime peers and Xray unchanged')
    print('CODE_BACKUP: '+str(backup))
if __name__=='__main__':main()
