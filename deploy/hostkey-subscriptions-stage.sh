#!/usr/bin/env bash
# Start only the read-only local subscription WSGI service; no Caddy/Xray edits.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_unit="$project_dir/deploy/caesar-vless-subscriptions.service"
target_unit="/etc/systemd/system/caesar-vless-subscriptions.service"

if [[ "$(id -u)" != "0" ]]; then
  echo "Run as root on HOSTKEY" >&2
  exit 2
fi
if [[ ! -f /var/lib/caesar-manager/clients.db ]]; then
  echo "Run hostkey-stage.sh first" >&2
  exit 2
fi
if [[ -e "$target_unit" ]] && ! cmp -s "$source_unit" "$target_unit"; then
  echo "Subscription unit already exists and differs; review manually" >&2
  exit 2
fi

install -m 0644 "$source_unit" "$target_unit"
systemctl daemon-reload
systemctl enable --now caesar-vless-subscriptions.service
systemctl is-active --quiet caesar-vless-subscriptions.service

# Nonexistent random token must return 404 rather than any customer record.
code="$(curl --max-time 5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5058/sub/invalid)"
if [[ "$code" != "404" ]]; then
  echo "Local subscription endpoint returned unexpected HTTP $code" >&2
  exit 1
fi
echo "SUBSCRIPTIONS_LOCAL_OK: Xray and Caddy are untouched"
