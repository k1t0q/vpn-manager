#!/usr/bin/env bash
set -euo pipefail
cd /opt/vpn-manager
test -f staging_api.py
cp staging_api.py "staging_api.py.bak.wg.$(date +%Y%m%d%H%M%S)"
curl -fsSL https://raw.githubusercontent.com/k1t0q/vpn-manager/sqlite-migration-staging/sqlite_wireguard_staging.py -o sqlite_wireguard_staging.py
python3 -m py_compile sqlite_wireguard_staging.py
python3 - <<'PY'
from pathlib import Path
p=Path("staging_api.py")
s=p.read_text()
needle="register_wireguard(app,authorized,db_connect)"
if needle not in s:
    s += chr(10)+"from sqlite_wireguard_staging import register as register_wireguard"+chr(10)+needle+chr(10)
    p.write_text(s)
PY
python3 -m py_compile staging_api.py
systemctl restart vpn-manager-staging
systemctl is-active --quiet vpn-manager-staging
curl -fsS https://vpn-staging.80-90-179-65.sslip.io/health
echo
echo "Staging WireGuard routes registered. Production service untouched."
