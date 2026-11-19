#!/usr/bin/env bash
# Install the CAESAR VLESS entitlement reconciler only after a pilot profile works.
set -euo pipefail

src=/srv/caesar-manager-src
unit_dir=/etc/systemd/system
service=caesar-xray-expiry.service
timer=caesar-xray-expiry.timer

test "$(id -u)" -eq 0 || { echo 'Run as root' >&2; exit 1; }
test -f /var/lib/caesar-manager/clients.db || { echo 'Missing CAESAR working database' >&2; exit 1; }
test -f /usr/local/etc/xray/config.json || { echo 'Missing production Xray config' >&2; exit 1; }
test -f "$src/deploy/$service" || { echo 'Pull the latest CAESAR migration branch first' >&2; exit 1; }

# Validate read-only first. Any unexpected inbound or invalid Xray config aborts.
python3 "$src/server/xray_hostkey.py" sync

for unit in "$service" "$timer"; do
    if [[ -e "$unit_dir/$unit" ]] && ! cmp -s "$src/deploy/$unit" "$unit_dir/$unit"; then
        echo "Existing custom unit differs; refusing to overwrite: $unit_dir/$unit" >&2
        exit 1
    fi
done

install -m 0644 "$src/deploy/$service" "$unit_dir/$service"
install -m 0644 "$src/deploy/$timer" "$unit_dir/$timer"
systemctl daemon-reload
# Run one guarded reconciliation before enabling future automatic checks.
systemctl start "$service"
systemctl enable --now "$timer"
systemctl is-active xray
systemctl is-active "$timer"
echo 'CAESAR_EXPIRY_TIMER_ACTIVE: changes to subscriptions apply within five minutes'
