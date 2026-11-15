"""Owner-run read-only infrastructure audit; publishes counts, never secrets.

Writes only a sanitized staging report and a private copy under /root.
Does not restart services, import application code, or change VPN/client state.
"""
import ast
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

BASE = Path('/opt/vpn-manager')
ROOT = Path('/var/lib/vpn-manager-awg-stage')


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15).stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sql_counts(path):
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as con:
        counts = {}
        for table in ('clients', 'client_history', 'awg_deleted_clients', 'subscription_notifications'):
            if con.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone():
                counts[table] = con.execute('SELECT count(*) FROM '+table).fetchone()[0]
        counts['integrity_ok'] = con.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        return counts


def api_writers():
    """Identify old API owners without publishing cmdlines or environments."""
    listeners = run(['ss', '-ltnpH'])
    result = []
    for line in listeners.splitlines():
        fields = line.split()
        if len(fields) < 4 or not re.search(r':(5050|5052)$', fields[3]):
            continue
        port = int(fields[3].rsplit(':', 1)[1])
        for pid in sorted(set(re.findall(r'pid=([0-9]+)', line))):
            proc = Path('/proc')/pid
            args = (proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
            cwd = (proc/'cwd').resolve()
            scripts = []
            for arg in args:
                if arg.endswith('.py') and not arg.startswith('-'):
                    path = Path(arg) if arg.startswith('/') else cwd/arg
                    if path.is_file() and str(path.resolve()).startswith(('/opt/vpn-manager/', '/opt/vpn-manager-panel-test/')):
                        scripts.append(path.resolve())
            units = [part for part in (proc/'cgroup').read_text().split('/') if part.strip().endswith('.service')]
            item = dict(port=port, pid=int(pid), service=units[-1].strip() if units else None, scripts=[])
            for path in scripts:
                tree = ast.parse(path.read_text())
                paths = sorted({n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith(('/opt/vpn-manager/', '/srv/vpn-manager-')) and n.value.endswith('.db')})
                upstreams = sorted({match for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) for match in re.findall(r'https?://127\.0\.0\.1:[0-9]{2,5}', n.value)})
                item['scripts'].append(dict(path=str(path), sha256=digest(path), database_paths=paths, loopback_upstreams=upstreams))
            result.append(item)
    return result


def main():
    if os.geteuid() != 0:
        raise SystemExit('Root required for read-only inspection')
    # The frozen parser is imported only from root-owned installed source.
    import sys
    code = Path('/usr/local/libexec/vpn-manager-awg-stage')
    if code.is_symlink() or code.stat().st_uid != 0 or code.stat().st_mode & 0o022:
        raise SystemExit('Unexpected frozen module directory')
    sys.path.insert(0, str(code))
    from awg_persistence import peer_blocks
    baseline = json.loads((ROOT/'baseline.json').read_text())
    if len(baseline['protected_ids']) != 59:
        raise SystemExit('Unexpected server identity; nothing published')
    addresses = run(['ip', '-4', '-o', 'address', 'show'])
    if not re.search(r'\binet 185\.23\.239\.142/', addresses):
        raise SystemExit('Server address mismatch; nothing published')
    _, blocks = peer_blocks(Path('/etc/amnezia/amneziawg/awg-test.conf').read_text())
    disk = {key: fields for key, fields, _ in blocks}
    live = set(run(['awg', 'show', 'awg-test', 'peers']).split())
    port = int(run(['awg', 'show', 'awg-test', 'listen-port']).strip())
    good = 0
    with sqlite3.connect((BASE/'data/clients-staging.db').as_uri()+'?mode=ro', uri=True) as con:
        ids = {str(row[0]) for row in con.execute('SELECT id FROM clients')}
    for cid in baseline['protected_ids']:
        cid = str(cid)
        meta = json.loads((BASE/'awg-clients'/cid/'metadata.json').read_text())
        key = meta['public_key']
        wanted = str(ipaddress.ip_address(meta['address']))+'/32'
        good += int(cid in ids and str(meta['client_id']) == cid and key in live and disk.get(key, {}).get('AllowedIPs') == wanted)
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), server_ip='185.23.239.142', interface='awg-test', udp_port=port,
                  production=sql_counts(BASE/'data/clients-staging.db'), staging=sql_counts(ROOT/'clients.db'),
                  protected_clients=59, protected_clients_matching_db_metadata_disk_runtime=good,
                  protected_disk_fields_unchanged=all(disk.get(k) == v for k, v in baseline['peers'].items()),
                  original_runtime_peers_preserved=set(baseline['runtime_peers']).issubset(live),
                  disk_peers=len(disk), runtime_peers=len(live), pending_operation=(ROOT/'operations/pending.json').exists())
    report['services'] = {}
    for name in ('awg-quick@awg-test.service', 'vpn-manager.service', 'vpn-manager-stage.service', 'vpn-manager-awg-readonly.service', 'xray.service', 'caddy.service'):
        text = run(['systemctl', 'show', name, '-p', 'ActiveState', '-p', 'SubState', '-p', 'UnitFileState', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'RemainAfterExit', '-p', 'FragmentPath', '-p', 'DropInPaths'])
        report['services'][name] = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    report['startup_references'] = []
    candidates = [Path('/etc/rc.local')]
    for folder in ('/etc/systemd/system', '/lib/systemd/system', '/etc/cron.d', '/etc/cron.daily', '/var/spool/cron/crontabs'):
        candidates.extend(p for p in Path(folder).rglob('*') if p.is_file() and not p.is_symlink())
    for path in candidates:
        try:
            if path.stat().st_size > 256000:
                continue
            text = path.read_text(errors='replace')
            if 'awg-test' in text:
                report['startup_references'].append(dict(path=str(path), has_awg_quick='awg-quick' in text, mentions_restart='restart' in text))
        except OSError:
            continue
    report['source_hashes'] = {}
    report['legacy_api_writers'] = api_writers()
    # Classification only: raw journal entries can contain command output.
    try:
        events = run(['journalctl', '-b', '-u', 'awg-quick@awg-test.service', '-o', 'json', '--no-pager'])
        messages = [json.loads(line).get('MESSAGE', '') for line in events.splitlines() if line.startswith('{')]
        report['awg_boot_evidence'] = dict(entries=len(messages), already_exists=any('already exists' in str(m).lower() for m in messages), start_recorded=any('started' in str(m).lower() for m in messages), stop_recorded=any('stopped' in str(m).lower() or 'stopping' in str(m).lower() for m in messages))
    except (OSError, subprocess.SubprocessError, ValueError):
        report['awg_boot_evidence'] = dict(unavailable=True)
    report['production_routes'] = []
    for path in (BASE/'app.py', BASE/'awg_adapter.py'):
        report['source_hashes'][str(path)] = digest(path)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('route', 'get', 'post', 'patch', 'delete') and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) and node.args[0].value.startswith('/'):
                report['production_routes'].append(dict(file=path.name, method=node.func.attr, route=node.args[0].value))
    public = Path('/srv/vpn-manager-stage/current/public')
    resolved = public.resolve()
    if not resolved.is_relative_to(Path('/srv/vpn-manager-stage/releases')):
        raise SystemExit('Unexpected staging report path')
    data = json.dumps(report, ensure_ascii=False, indent=2).encode()
    private = Path('/root/vpn-manager-backups')/('audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    private.mkdir(mode=0o700, parents=True)
    for target, mode in ((private/'server-audit.json', 0o600), (resolved/'server-audit.json', 0o644)):
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.audit-')
        with os.fdopen(fd, 'wb') as out:
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    print('READ_ONLY_AUDIT_READY; matched_protected_clients='+str(good)+'; VPN unchanged')


if __name__ == '__main__':
    main()
