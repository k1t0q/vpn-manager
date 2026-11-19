#!/usr/bin/env bash
# Promote the HOSTKEY VPN MANAGER pilot to full guarded client management.
# Xray/Caddy configurations and client credentials are deliberately untouched.
set -euo pipefail

src=/srv/caesar-manager-src
db=/var/lib/caesar-manager/clients.db
env_path=/etc/caesar/manager-panel.env
panel_unit=/etc/systemd/system/caesar-vless-panel.service
expected_ids=88,92,93

if [[ "$(id -u)" != 0 ]]; then
  echo 'Run on HOSTKEY as root' >&2
  exit 2
fi
test -f "$src/server/hostkey_panel_ops.py"
test -f "$src/server/hostkey_bulk_migrate.py"
test -f "$db"
test -f "$env_path"
test -f "$panel_unit"
if [[ -L "$db" || -L "$env_path" || -L "$panel_unit" ]]; then
  echo 'Unexpected symlink; refusing production promotion' >&2
  exit 2
fi
grep -qx 'ReadWritePaths=/var/lib/caesar-manager' "$panel_unit"
if ! grep -qx 'CAESAR_PANEL_PILOT_IDS=93' "$env_path"; then
  echo 'Expected existing test-client pilot is missing' >&2
  exit 2
fi
if grep -Ev '^(CAESAR_PANEL_PILOT_IDS=93|CAESAR_PANEL_MODE=full)$' "$env_path" | grep -q .; then
  echo 'Unknown panel configuration; manual review required' >&2
  exit 2
fi
for service in xray caddy caesar-vless-panel caesar-vless-subscriptions caesar-xray-expiry.timer; do
  systemctl is-active --quiet "$service" || { echo "Inactive: $service" >&2; exit 2; }
done

(cd "$src" && python3 -m unittest discover -s tests -p 'hostkey*test.py' -q)
python3 - "$db" "$expected_ids" <<'PY'
import sqlite3,sys
from pathlib import Path
sys.path.insert(0, '/srv/caesar-manager-src')
from server.hostkey_bulk_migrate import plan,parse_ids
from server.xray_hostkey import active_subscription,check_database
db=Path(sys.argv[1]); tests=parse_ids(sys.argv[2]); healthy,count=check_database(db)
assert healthy and count == 70, 'Unexpected billing database'
with sqlite3.connect(db) as con:
    p=plan(con,tests)
    assert (p['real_clients'],p['active_real_clients'],p['inactive_real_clients'],
            p['clients_to_register'],p['already_with_two_keys']) == (67,60,7,0,67), p
    rows=con.execute('''SELECT c.id,c.period,c.end_date,d.kind,d.deployed
                          FROM clients c JOIN caesar_vless_devices d ON c.id=d.client_id
                         ORDER BY c.id,d.kind''').fetchall()
    real={cid:{} for cid, in con.execute('SELECT id FROM clients') if cid not in tests}
    for cid,period,end,kind,deployed in rows:
        if cid in real: real[cid][kind]=(bool(deployed),active_subscription(period,end))
    for cid,devices in real.items():
        assert set(devices)=={'phone','pc'}, f'Missing device for client {cid}'
        for deployed,active in devices.values():
            assert deployed == active, f'Xray not reconciled for client {cid}'
print('FULL_PANEL_PREFLIGHT_OK: 67 real clients; 60 active, 7 expired; every device reconciled')
PY

# Preserve the exact pre-promotion data and service configuration.
stamp="$(date +%Y%m%d-%H%M%S)"
before="/root/caesar-panel-env-before-full-$stamp"
install -m 0600 "$env_path" "$before"
db_backup="$(python3 -c 'import sys;from pathlib import Path;sys.path.insert(0,"/srv/caesar-manager-src");from server.hostkey_bulk_migrate import private_backup;print(private_backup(Path("/var/lib/caesar-manager/clients.db"),Path("/root/caesar-panel-full-backups")))')"

install -d -m 0700 /etc/caesar
new_env="$(mktemp /etc/caesar/.manager-panel.XXXXXXXX)"
chmod 0600 "$new_env"
printf 'CAESAR_PANEL_PILOT_IDS=93\nCAESAR_PANEL_MODE=full\n' > "$new_env"
mv "$new_env" "$env_path"

rollback() {
  install -m 0600 "$before" "$env_path"
  systemctl restart caesar-vless-panel || true
}
if ! systemctl restart caesar-vless-panel || ! systemctl is-active --quiet caesar-vless-panel; then
  rollback
  echo 'Panel failed to restart; pilot environment restored' >&2
  exit 1
fi
if ! curl -fsS --max-time 5 http://127.0.0.1:5059/manager-test/health | python3 -c \
  'import json,sys;h=json.load(sys.stdin);assert h["ok"] and h["full_access"] and not h["read_only"]'; then
  rollback
  echo 'Panel health failed; pilot environment restored' >&2
  exit 1
fi

for service in xray caddy caesar-xray-expiry.timer; do
  systemctl is-active --quiet "$service"
done
echo 'PANEL_FULL_MANAGEMENT_READY: 67 real clients; Xray/Caddy unchanged'
echo "PRIVATE_DB_BACKUP: $db_backup"
echo "PILOT_ENV_BACKUP: $before"
