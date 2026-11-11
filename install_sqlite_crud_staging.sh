#!/usr/bin/env bash
set -euo pipefail
cd /opt/vpn-manager
test -f staging_api.py
test -f data/clients-staging.db
cp staging_api.py "staging_api.py.bak.$(date +%Y%m%d%H%M%S)"
curl --fail --silent --show-error --location --output sqlite_client_crud.py.new \
  https://raw.githubusercontent.com/k1t0q/vpn-manager/sqlite-migration-staging/sqlite_client_crud.py
python3 -m py_compile sqlite_client_crud.py.new
mv sqlite_client_crud.py.new sqlite_client_crud.py
python3 - <<'PY'
from pathlib import Path
p=Path('staging_api.py')
s=p.read_text()
line='from sqlite_client_crud import register as register_crud\nregister_crud(app,authorized,db_connect)\n'
if 'register_crud(app,authorized,db_connect)' not in s:
    marker="if __name__=='__main__':"
    if marker in s:
        s=s.replace(marker,line+chr(10)+marker)
    else:
        s=s.rstrip()+chr(10)+chr(10)+line
    p.write_text(s)
PY
python3 -m py_compile staging_api.py sqlite_client_crud.py
systemctl restart vpn-manager-staging
systemctl is-active --quiet vpn-manager-staging
curl --fail --silent --show-error https://vpn-staging.80-90-179-65.sslip.io/health
echo
echo 'Staging CRUD routes installed. Production service untouched.'
