#!/usr/bin/env bash
# Publish isolated read-only VPN MANAGER pilot without touching Xray or billing.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_unit="$project_dir/deploy/caesar-vless-panel.service"
target_unit="/etc/systemd/system/caesar-vless-panel.service"
code_file="/var/lib/caesar-manager/panel-code"

if [[ "$(id -u)" != "0" ]]; then
  echo 'Root required on HOSTKEY' >&2
  exit 2
fi
test -f '/var/lib/caesar-manager/clients.db'
test -f "$project_dir/server/hostkey_panel.py"
test -f "$project_dir/server/hostkey_panel.js"
test -f "$project_dir/styles.css"
id caesar-manager >/dev/null
if [[ -e "$target_unit" ]] && ! cmp -s "$target_unit" "$source_unit"; then
  echo 'Panel unit exists and differs; review first' >&2
  exit 2
fi
# Reserve port 5059 for this service only, with no clobbering other apps.
if [[ ! -e "$target_unit" ]] && ss -Hltpn '( sport = :5059 )' | grep -q .; then
  echo 'Port 5059 already in use; refusing installation' >&2
  exit 2
fi

new_code=0
if [[ ! -e "$code_file" ]]; then
  umask 077
  install -m 0600 -o caesar-manager -g caesar-manager /dev/null "$code_file"
  /usr/bin/python3 -c 'import secrets,sys;sys.stdout.write(secrets.token_urlsafe(36)+"\n")' > "$code_file"
  new_code=1
fi
chown caesar-manager:caesar-manager "$code_file"
chmod 0600 "$code_file"
install -m 0644 "$source_unit" "$target_unit"
systemctl daemon-reload
systemctl enable --now caesar-vless-panel.service
systemctl is-active --quiet caesar-vless-panel.service
status="$(curl --max-time 5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5059/manager-test/health)"
if [[ "$status" != "200" ]]; then
  echo "Panel health returned HTTP $status; Caddy untouched" >&2
  exit 1
fi
python3 "$project_dir/deploy/hostkey-panel-caddy.py" --apply
systemctl is-active --quiet xray
systemctl is-active --quiet caddy
systemctl is-active --quiet caesar-vless-subscriptions
systemctl is-active --quiet caesar-xray-expiry.timer
echo 'CAESAR_MANAGER_PILOT_READY: https://vpn.caesarbot.ru/manager-test/'
if [[ "$new_code" == '1' ]]; then
  echo 'LOGIN CODE GENERATED. Read it locally on HOSTKEY with: sudo cat /var/lib/caesar-manager/panel-code'
else
  echo 'Existing admin code preserved: /var/lib/caesar-manager/panel-code'
fi
