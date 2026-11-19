#!/usr/bin/env bash
# Upgrade the read-only panel to an explicitly allowlisted write pilot.
# No client mutation or Xray/Caddy change occurs during this installation.
set -euo pipefail

src=/srv/caesar-manager-src
unit=caesar-vless-panel.service
unit_src="$src/deploy/$unit"
unit_dst="/etc/systemd/system/$unit"
env_path=/etc/caesar/manager-panel.env
pilot_id=""
unit_backup=""

if [[ "$(id -u)" != 0 ]]; then
  echo 'Run this on HOSTKEY as root' >&2
  exit 2
fi
if [[ "$#" == 2 && "$1" == '--pilot-id' && "$2" =~ ^[1-9][0-9]*$ ]]; then
  pilot_id="$2"
elif [[ "$#" != 0 ]]; then
  echo 'Usage: bash hostkey-panel-actions-stage.sh [--pilot-id 93]' >&2
  exit 2
fi
test -f "$unit_src"
test -f "$unit_dst"
test -f /var/lib/caesar-manager/clients.db
test -f "$src/server/hostkey_panel_ops.py"
if [[ -L "$unit_dst" || -L "$env_path" ]]; then
  echo 'Unexpected symlink in service or panel config; manual review required' >&2
  exit 2
fi
python3 -m py_compile "$src/server/hostkey_panel.py" "$src/server/hostkey_panel_ops.py"
(cd "$src" && python3 -m unittest discover -s tests -p 'hostkey_panel*test.py' -q)

# Pilot mode must name an existing *test-labelled* client, not a paying user.
if [[ -n "$pilot_id" ]]; then
  python3 -c 'import sqlite3,sys; c=sqlite3.connect("/var/lib/caesar-manager/clients.db"); r=c.execute("SELECT name FROM clients WHERE id=?", (int(sys.argv[1]),)).fetchone(); assert r and any(word in (r[0] or "").casefold() for word in ("test", "тест")), "Pilot ID must name a test client"' "$pilot_id"
fi

# Snapshot the database before any feature is made available.
stamp="$(date +%Y%m%d-%H%M%S)"
backup="/root/caesar-clients-before-panel-actions-$stamp.db"
if [[ ! -e "$backup" ]]; then
  umask 077
  python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); s.close(); d.close()' /var/lib/caesar-manager/clients.db "$backup"
  chmod 0600 "$backup"
fi

install -d -m 0700 /etc/caesar
if [[ -n "$pilot_id" ]]; then
  if [[ -f "$env_path" ]] && ! grep -Eq '^CAESAR_PANEL_PILOT_IDS=[0-9,]+$' "$env_path"; then
    echo 'Unexpected existing panel environment; manual review required' >&2
    exit 2
  fi
  printf 'CAESAR_PANEL_PILOT_IDS=%s\n' "$pilot_id" > "$env_path"
  chmod 0600 "$env_path"
fi
unit_backup="/root/caesar-panel-service-before-$stamp"
install -m 0600 "$unit_dst" "$unit_backup"
install -m 0644 "$unit_src" "$unit_dst"
systemctl daemon-reload
rollback_unit() {
  install -m 0644 "$unit_backup" "$unit_dst"
  systemctl daemon-reload
  systemctl restart caesar-vless-panel || true
}
if ! systemctl restart caesar-vless-panel || ! systemctl is-active --quiet caesar-vless-panel; then
  rollback_unit
  echo 'Panel failed to start; previous service unit restored' >&2
  exit 1
fi
status="$(curl --max-time 5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5059/manager-test/health)"
if [[ "$status" != 200 ]]; then
  rollback_unit
  echo "Panel health failed: HTTP $status" >&2
  exit 1
fi
systemctl is-active --quiet xray
systemctl is-active --quiet caddy
systemctl is-active --quiet caesar-xray-expiry.timer
if [[ -n "$pilot_id" ]]; then
  echo "PANEL_ACTIONS_PILOT_READY: test client #$pilot_id; others unchanged"
else
  echo 'PANEL_ACTIONS_STAGED_READONLY: choose an explicit test client before enabling changes'
fi
echo "PRIVATE_BACKUP: $backup"
