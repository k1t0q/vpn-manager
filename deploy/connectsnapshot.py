"""Root read-only snapshot of exactly one owner-approved pilot profile."""
from datetime import datetime, timezone
from pathlib import Path
import grp, hashlib, json, os, sqlite3, tempfile, time

ROOT=Path('/var/lib/vpn-manager-connect')


def write(path,data):
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.snapshot-')
    with os.fdopen(fd,'wb') as out:
        os.fchmod(out.fileno(),0o640);os.fchown(out.fileno(),0,grp.getgrnam('vpn-connect').gr_gid)
        out.write(data);out.flush();os.fsync(out.fileno())
    os.replace(name,path)


def main():
    policy=Path('/etc/vpn-manager-connect-pilot.json')
    if not policy.exists():return
    if policy.is_symlink() or policy.stat().st_uid!=0 or policy.stat().st_mode & 0o077:raise SystemExit('Private root pilot policy required')
    cid=json.loads(policy.read_text())['client_id']
    if type(cid) is not int or cid<=0:raise SystemExit('Invalid pilot ID')
    result=dict(client_id=cid,available=False,updated_at=time.time(),sha256=None)
    with sqlite3.connect('file:/opt/vpn-manager/data/clients-staging.db?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        row=con.execute('SELECT id,period,end_date FROM clients WHERE id=?',(cid,)).fetchone()
        removed=con.execute('SELECT 1 FROM awg_deleted_clients WHERE client_id=?',(str(cid),)).fetchone()
    if row and not removed:
        directory=Path('/opt/vpn-manager/awg-clients')/str(cid)
        meta_path=directory/'metadata.json';profile=directory/'client.conf'
        if not meta_path.is_symlink() and not profile.is_symlink() and profile.is_file():
            before=meta_path.read_bytes();meta=json.loads(before);data=profile.read_bytes()
            end=row['end_date']
            valid=row['period']=='lifetime' or (end and datetime.fromisoformat(end.replace('Z','+00:00') if 'T' in end else end+'T00:00:00+03:00')>datetime.now(timezone.utc))
            result['available']=bool(valid and meta.get('enabled') is not False and meta_path.read_bytes()==before and str(meta['client_id'])==str(cid))
            result['sha256']=hashlib.sha256(data).hexdigest()
            if result['available']:write(ROOT/'material/client.conf',data)
    write(ROOT/'material/status.json',json.dumps(result).encode())


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Pilot snapshot unavailable; delivery fails closed') from None
