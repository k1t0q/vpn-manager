#!/usr/bin/env bash
# Enable rapid, unprivileged panel-to-root Xray reconciliation on HOSTKEY.
# The existing five-minute timer remains the fallback; VPN keys stay unchanged.
set -euo pipefail

src=/srv/caesar-manager-src
env_path=/etc/caesar/manager-panel.env
unit_path=/etc/systemd/system/caesar-xray-fast-sync.path
expiry_unit=/etc/systemd/system/caesar-xray-expiry.service
signal_path=/var/lib/caesar-manager/fast-sync.signal

if [[ $(id -u) -ne 0 ]]; then
  echo 'Run as root on HOSTKEY' >&2
  exit 2
fi
test -f "$env_path"
test -f "$expiry_unit"
test -f "$src/deploy/caesar-xray-fast-sync.path"
test -f "$src/server/hostkey_panel.py"
if [[ -L "$env_path" || -L "$expiry_unit" || -L "$signal_path" || -L "$unit_path" ]]; then
  echo 'Unexpected symlink; refusing production changes' >&2
  exit 2
fi
grep -qx 'CAESAR_PANEL_MODE=full' "$env_path" || {
  echo 'Enable the production VPN MANAGER before installing access control' >&2
  exit 2
}
if grep -Ev '^(CAESAR_PANEL_MODE=full|CAESAR_XRAY_FAST_SYNC_SIGNAL=/var/lib/caesar-manager/fast-sync.signal)$' "$env_path" | grep -q .; then
  echo 'Unexpected panel environment entries' >&2
  exit 2
fi
grep -Fxq 'ExecStart=/usr/bin/python3 /srv/caesar-manager-src/server/xray_hostkey.py sync --apply' "$expiry_unit" || {
  echo 'Unexpected expiry service; refusing to install fast-sync watcher' >&2
  exit 2
}
if [[ -e "$unit_path" ]] && ! cmp -s "$src/deploy/caesar-xray-fast-sync.path" "$unit_path"; then
  echo 'An existing fast-sync path unit differs from the expected version' >&2
  exit 2
fi
for unit in xray caddy caesar-vless-panel caesar-vless-subscriptions caesar-xray-expiry.timer; do
  systemctl is-active --quiet "$unit" || { echo "Service inactive: $unit" >&2; exit 2; }
done
(cd "$src" && python3 -m unittest discover -s tests -p 'hostkey*test.py' -q)
systemd-analyze verify "$src/deploy/caesar-xray-fast-sync.path"

stamp=$(date +%Y%m%d-%H%M%S)
backup="/root/caesar-panel-env-before-access-$stamp"
install -m 0600 "$env_path" "$backup"
if [[ -e "$signal_path" && ! -f "$signal_path" ]]; then
  echo 'Fast-sync signal is not a regular file' >&2
  exit 2
fi
install -o caesar-manager -g caesar-manager -m 0600 /dev/null "$signal_path"
install -m 0644 "$src/deploy/caesar-xray-fast-sync.path" "$unit_path"
systemctl daemon-reload
systemctl enable --now caesar-xray-fast-sync.path

env_tmp=$(mktemp /etc/caesar/.manager-access.XXXXXXXX)
chmod 0600 "$env_tmp"
printf 'CAESAR_PANEL_MODE=full\nCAESAR_XRAY_FAST_SYNC_SIGNAL=/var/lib/caesar-manager/fast-sync.signal\n' > "$env_tmp"
mv "$env_tmp" "$env_path"
if ! systemctl restart caesar-vless-panel || ! systemctl is-active --quiet caesar-vless-panel || \
   ! curl -fsS --max-time 5 http://127.0.0.1:5059/manager/health | python3 -c \
      'import json,sys;s=json.load(sys.stdin);assert s["ok"] and s["full_access"] and s["fast_sync"]'; then
  install -m 0600 "$backup" "$env_path"
  systemctl restart caesar-vless-panel || true
  echo 'Panel health failed. Original environment restored; timer unaffected.' >&2
  exit 1
fi
for unit in xray caddy caesar-xray-expiry.timer caesar-xray-fast-sync.path; do
  systemctl is-active --quiet "$unit"
done
echo 'CAESAR_ACCESS_CONTROL_READY: https://vpn.caesarbot.ru/manager/'
echo "PRIVATE_BACKUP: $backup"
