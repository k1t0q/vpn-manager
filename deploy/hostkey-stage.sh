#!/usr/bin/env bash
# Stage only: does not restart Xray/Caddy, change DNS, or publish profiles.
set -euo pipefail

source_backup="${1:?Usage: hostkey-stage.sh /root/verified-clients-backup.db}"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination="/var/lib/caesar-manager/clients.db"

if [[ "$(id -u)" != "0" ]]; then
  echo "Run as root on HOSTKEY" >&2
  exit 2
fi
if [[ ! -f "$source_backup" || -L "$source_backup" ]]; then
  echo "Expected existing regular SQLite backup: $source_backup" >&2
  exit 2
fi
if [[ -e "$destination" || -L "$destination" ]]; then
  echo "Working database already exists; refusing to overwrite: $destination" >&2
  exit 2
fi

python3 "$project_dir/server/xray_hostkey.py" inventory --db "$source_backup"
if ! id -u caesar-manager >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/caesar-manager --shell /usr/sbin/nologin caesar-manager
fi
install -d -o caesar-manager -g caesar-manager -m 0700 /var/lib/caesar-manager
install -o caesar-manager -g caesar-manager -m 0600 "$source_backup" "$destination"
python3 "$project_dir/server/xray_hostkey.py" inventory --db "$destination"

echo "STAGED_OK: existing Xray/Caddy/services have not been modified"
