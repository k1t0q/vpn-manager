#!/usr/bin/env bash
# Publish the full CAESAR VPN MANAGER at /manager/ without changing VLESS keys.
set -euo pipefail

src=/srv/caesar-manager-src
env_path=/etc/caesar/manager-panel.env
panel_unit=/etc/systemd/system/caesar-vless-panel.service
source_unit="$src/deploy/caesar-vless-panel.service"

if [[ "$(id -u)" != 0 ]]; then
  echo 'Run on HOSTKEY as root' >&2
  exit 2
fi
test -f "$env_path"
test -f "$panel_unit"
test -f "$source_unit"
if [[ -L "$env_path" || -L "$panel_unit" ]]; then
  echo 'Unexpected symlink in system settings' >&2
  exit 2
fi
if ! grep -qx 'CAESAR_PANEL_PILOT_IDS=93' "$env_path" && ! grep -qx 'CAESAR_PANEL_MODE=full' "$env_path"; then
  echo 'Unexpected panel mode; refusing promotion' >&2
  exit 2
fi
if grep -Ev '^(CAESAR_PANEL_PILOT_IDS=93|CAESAR_PANEL_MODE=full|CAESAR_XRAY_FAST_SYNC_SIGNAL=/var/lib/caesar-manager/fast-sync.signal)$' "$env_path" | grep -q .; then
  echo 'Unrecognized panel settings; refusing promotion' >&2
  exit 2
fi
for unit in xray caddy caesar-vless-panel caesar-vless-subscriptions caesar-xray-expiry.timer; do
  systemctl is-active --quiet "$unit" || { echo "Service inactive: $unit" >&2; exit 2; }
done

(cd "$src" && python3 -m unittest discover -s tests -p 'hostkey*test.py' -q)
python3 - <<'PY'
import sqlite3,sys
sys.path.insert(0, '/srv/caesar-manager-src')
from server.hostkey_bulk_migrate import plan
from server.xray_hostkey import active_subscription,check_database
db='/var/lib/caesar-manager/clients.db'
healthy,count=check_database(__import__('pathlib').Path(db))
assert healthy and count in (67,70), 'Unexpected database size/integrity'
with sqlite3.connect(db) as con:
    ids={88,92,93} if count==70 else set()
    state=plan(con,frozenset(ids))
    assert (state['real_clients'],state['active_real_clients'],state['inactive_real_clients'],
            state['clients_to_register'])==(67,60,7,0), state
    rows=con.execute('''SELECT c.id,c.period,c.end_date,d.deployed FROM clients c
                          JOIN caesar_vless_devices d ON d.client_id=c.id''').fetchall()
    real=[r for r in rows if r[0] not in {88,92,93}]
    assert len(real)==134 and all(bool(deployed)==active_subscription(period,end)
                                  for _,period,end,deployed in real), 'Xray not reconciled'
print('PRODUCTION_PREFLIGHT_OK: 67 real clients, full keys and billing intact')
PY

python3 "$src/deploy/hostkey-panel-production-caddy.py"
stamp="$(date +%Y%m%d-%H%M%S)"
env_backup="/root/caesar-panel-env-before-production-$stamp"
unit_backup="/root/caesar-panel-unit-before-production-$stamp"
install -m 0600 "$env_path" "$env_backup"
install -m 0600 "$panel_unit" "$unit_backup"

# Change routing first: old /manager-test continues to work until panel reload.
python3 "$src/deploy/hostkey-panel-production-caddy.py" --apply

new_env="$(mktemp /etc/caesar/.manager-production.XXXXXXXX)"
chmod 0600 "$new_env"
printf 'CAESAR_PANEL_MODE=full\n' > "$new_env"
if grep -qx 'CAESAR_XRAY_FAST_SYNC_SIGNAL=/var/lib/caesar-manager/fast-sync.signal' "$env_path"; then
  printf 'CAESAR_XRAY_FAST_SYNC_SIGNAL=/var/lib/caesar-manager/fast-sync.signal\n' >> "$new_env"
fi
mv "$new_env" "$env_path"
install -m 0644 "$source_unit" "$panel_unit"
systemctl daemon-reload
if ! systemctl restart caesar-vless-panel || ! systemctl is-active --quiet caesar-vless-panel; then
  install -m 0600 "$env_backup" "$env_path"
  install -m 0644 "$unit_backup" "$panel_unit"
  systemctl daemon-reload
  systemctl restart caesar-vless-panel || true
  echo 'Panel restart failed; original environment and unit restored' >&2
  exit 1
fi
if ! curl -fsS --max-time 5 http://127.0.0.1:5059/manager/health | python3 -c \
  'import json,sys;h=json.load(sys.stdin);assert h["ok"] and h["full_access"] and not h["read_only"]'; then
  echo 'Production panel health failed; stop and investigate before client operations' >&2
  exit 1
fi
for unit in xray caddy caesar-xray-expiry.timer; do
  systemctl is-active --quiet "$unit"
done
echo 'CAESAR_MANAGER_PRODUCTION_READY: https://vpn.caesarbot.ru/manager/'
echo "PRIVATE_PANEL_BACKUPS: $env_backup $unit_backup"
