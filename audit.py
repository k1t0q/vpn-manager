"""Read-only live audit; optional protected backups. Never modifies VPN or source DB."""
import argparse, datetime, hashlib, json, os, pathlib, sqlite3, subprocess, tarfile
P = pathlib.Path
ROOT = P('/opt/vpn-manager')
DB = ROOT / 'data/clients-staging.db'
CONF = P('/etc/amnezia/amneziawg/awg-test.conf')
def command(args):
    r = subprocess.run(args, capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup', action='store_true')
    args = parser.parse_args()
    con = sqlite3.connect(DB.as_uri()+'?mode=ro', uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    rows = con.execute('select * from clients').fetchall()
    metas = [json.loads(p.read_text()) for p in (ROOT/'awg-clients').glob('*/metadata.json')]
    rc, peers = command(['awg','show','awg-test','peers'])
    config = CONF.read_text()
    report = {
        'clients': len(rows), 'metadata': len(metas),
        'live_peers': len(peers.splitlines()), 'awg_rc': rc,
        'config_peers': config.count('[Peer]'),
        'db_columns': list(rows[0].keys()) if rows else [],
        'metadata_fields': sorted(set().union(*(m.keys() for m in metas))),
        'history_count': con.execute('select count(*) from client_history').fetchone()[0],
        'quick_active': command(['systemctl','is-active','awg-quick@awg-test'])[1].strip(),
        'quick_enabled': command(['systemctl','is-enabled','awg-quick@awg-test'])[1].strip(),
        'vpn_manager_active': command(['systemctl','is-active','vpn-manager'])[1].strip(),
    }
    units=[]
    for folder in ['/etc/systemd/system','/etc/cron.d']:
        for p in P(folder).rglob('*'):
            if p.is_file() and not p.is_symlink():
                try:
                    text=p.read_text()
                    if 'awg-test' in text or 'vpn-manager' in text:
                        units.append(str(p))
                except (OSError, UnicodeError): pass
    report['startup_and_jobs']=units
    if args.backup:
        if len(rows)!=59 or rc or report['vpn_manager_active']!='active':
            raise SystemExit('Backup identity gate failed')
        os.umask(0o077)
        dest=P('/root/vpn-manager-backups')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        dest.mkdir(parents=True,mode=0o700)
        target=sqlite3.connect(dest/'clients-staging.db')
        con.backup(target)
        check=target.execute('pragma integrity_check').fetchone()[0]
        count=target.execute('select count(*) from clients').fetchone()[0]
        target.close()
        if check!='ok' or count!=59: raise SystemExit('Backup integrity failed')
        with tarfile.open(dest/'server-files.tar.gz','w:gz') as tar:
            for source in [ROOT,P('/opt/vpn-manager-panel-test'),P('/var/www/vpn-manager-test'),P('/etc/caddy'),P('/etc/amnezia'),P('/etc/systemd/system'),P('/etc/cron.d')]:
                if source.exists():
                    tar.add(source,arcname=str(source).lstrip('/'),filter=lambda info: None if '/.venv/' in info.name or '/__pycache__/' in info.name or info.name.endswith(('.db','.db-wal','.db-shm')) else info)
        (dest/'audit.json').write_text(json.dumps(report,indent=2))
        report['backup']=str(dest)
        report['backup_integrity']=check
    print(json.dumps(report,indent=2))
if __name__=='__main__': main()
