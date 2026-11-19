#!/usr/bin/env bash
# Stage a read-only Xray journal monitor. Never modifies Xray, keys or Caddy.
set -euo pipefail
umask 077

src=/srv/caesar-manager-src
state=/var/lib/caesar-manager
risk_dir=/var/lib/caesar-risk-monitor
for name in xray caddy caesar-vless-panel caesar-vless-subscriptions caesar-xray-expiry.timer; do
  systemctl is-active --quiet "$name" || { echo "Required service inactive: $name" >&2; exit 2; }
done
if [[ $(id -u) -ne 0 ]]; then
  echo 'Run as root on HOSTKEY' >&2
  exit 2
fi
test -d "$src/.git"
test -f "$state/clients.db"
test -f "$src/server/hostkey_risk.py"
test -f /usr/local/etc/xray/config.json

# If access logs are disabled or configured to a separate file, stop here.
# Enabling any Xray logging is a separate operator decision.
python3 "$src/server/hostkey_risk.py" check
python3 -m py_compile "$src/server/hostkey_risk.py" "$src/server/hostkey_panel.py" "$src/server/hostkey_push.py"
(cd "$src" && python3 -m unittest discover -s tests -p 'hostkey*_test.py' -q)
python3 "$src/deploy/hostkey-expiry-verify.py"
for name in caesar-hostkey-risk.service caesar-hostkey-risk.timer; do
  target="/etc/systemd/system/$name"
  source="$src/deploy/$name"
  [[ ! -L "$target" ]] || { echo "Refusing unexpected symlink: $target" >&2; exit 2; }
  if [[ -e "$target" ]] && ! cmp -s "$source" "$target"; then
    echo "Existing unit differs: $target; refusing to overwrite" >&2
    exit 2
  fi
  systemd-analyze verify "$source"
done

[[ ! -L "$risk_dir" ]] || { echo "Refusing risk directory symlink: $risk_dir" >&2; exit 2; }
if [[ -e "$risk_dir" && ! -d "$risk_dir" ]]; then
  echo "Risk storage is not a directory: $risk_dir" >&2
  exit 2
fi
install -d -o root -g caesar-manager -m 0750 "$risk_dir"

for name in caesar-hostkey-risk.service caesar-hostkey-risk.timer; do
  install -m 0644 "$src/deploy/$name" "/etc/systemd/system/$name"
done
systemctl daemon-reload
systemctl start caesar-hostkey-risk.service
systemctl enable --now caesar-hostkey-risk.timer
systemctl is-active --quiet caesar-hostkey-risk.timer

# Confirm that the regular unprivileged panel account can read the summaries.
runuser -u caesar-manager -- /usr/bin/python3 "$src/server/hostkey_risk.py" summary
systemctl restart caesar-vless-panel
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:5059/manager/health | python3 -c 'import sys,json; assert json.load(sys.stdin)["ok"]' 2>/dev/null; then
    echo 'CAESAR_HOSTKEY_RISK_READY: https://vpn.caesarbot.ru/manager/'
    echo 'VPN services and device keys untouched. Risk is heuristic, not proof.'
    exit 0
  fi
  sleep 1
done
echo 'Panel health did not recover; inspect journalctl -u caesar-vless-panel' >&2
exit 2
