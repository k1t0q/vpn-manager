"""Read-only client snapshot; never writes working SQLite, metadata or VPN state.

The same existing awg_profile_labels registry used by the manager is read in
the client database transaction. No API token or new label assignment is used.
"""
from pathlib import Path
import fcntl, grp, hashlib, json, os, sqlite3, tempfile, time

ROOT=Path('/var/lib/vpn-manager-connect')
DB=Path('/opt/vpn-manager/data/clients-staging.db')
PROFILES=Path('/opt/vpn-manager/awg-clients')
def write(path,data,gid):
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.portal-')
    try:
        with os.fdopen(fd,'wb') as out:
            os.fchmod(out.fileno(),0o640);os.fchown(out.fileno(),0,gid)
            out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def build(db_path,profiles,labels,material,gid):
    result={}
    with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        con.execute('BEGIN')
        if labels is None:labels={str(r['client_id']):r['label_number'] for r in con.execute('SELECT client_id,label_number FROM awg_profile_labels')}
        if len(set(labels.values()))!=len(labels):raise ValueError('Duplicate labels')
        rows=con.execute('SELECT id,wg_client_id,period,start_date,end_date FROM clients WHERE id NOT IN (SELECT client_id FROM awg_deleted_clients)').fetchall()
        for row in rows:
            cid=str(row['id']);peer=str(row['wg_client_id'] or '')
            # wg_client_id retains a legacy UUID for migrated clients. It is
            # never a filesystem path. The local numeric DB ID is explicitly
            # verified against metadata.client_id before reading its profile.
            if not cid.isdigit() or not peer or cid not in labels:continue
            folder=profiles/cid;meta_path=folder/'metadata.json';profile=folder/'client.conf'
            if folder.is_symlink() or meta_path.is_symlink() or profile.is_symlink() or not profile.is_file():continue
            before=meta_path.read_bytes();meta=json.loads(before);data=profile.read_bytes()
            if meta_path.read_bytes()!=before or str(meta.get('client_id'))!=cid:continue
            number=labels[cid]
            if type(number) is not int or number<=0:raise ValueError('Invalid profile label')
            entry=dict(number=number,period=row['period'],start_date=row['start_date'],end_date=row['end_date'],enabled=meta.get('enabled') is not False,sha256=hashlib.sha256(data).hexdigest())
            target=material/(cid+'.conf')
            if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=entry['sha256']:write(target,data,gid)
            result[cid]=entry
    write(material/'portal.json',json.dumps(dict(version=2,updated_at=time.time(),clients=result)).encode(),gid)
    return result

def main():
    with open(ROOT/'material/.snapshot.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        build(DB,PROFILES,None,ROOT/'material',grp.getgrnam('vpn-connect').gr_gid)

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Portal snapshot unavailable; profile delivery fails closed') from None
