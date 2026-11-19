#!/usr/bin/env bash
# Install Web Push without granting public internet access to the admin panel.
set -euo pipefail
umask 077

src=/srv/caesar-manager-src
venv=/opt/caesar-push-venv
state=/var/lib/caesar-manager
private="$state/push-private.pem"
public="$state/push-public.key"

if [[ $(id -u) -ne 0 ]]; then
  echo 'Run as root on HOSTKEY' >&2
  exit 2
fi
for unit in xray caddy caesar-vless-panel caesar-vless-subscriptions caesar-xray-expiry.timer; do
  systemctl is-active --quiet "$unit" || { echo "Required service inactive: $unit" >&2; exit 2; }
done
test -d "$src/.git"
test -f "$state/clients.db"
test -f "$src/server/hostkey_push.py"
python3 "$src/deploy/hostkey-expiry-verify.py"
for entry in "$private" "$public" \
  /etc/systemd/system/caesar-hostkey-push.service /etc/systemd/system/caesar-hostkey-push.timer; do
  [[ ! -L "$entry" ]] || { echo "Unexpected symlink: $entry" >&2; exit 2; }
done

(cd "$src" && python3 -m unittest discover -s tests -p 'hostkey_push_test.py' -q)
# Only the VAPID credentials require umask 077. The dedicated root-owned
# runtime under /opt must remain readable/executable by caesar-manager.
if [[ -L "$venv" || ( -e "$venv" && ! -d "$venv" ) ]]; then
  echo "Unexpected push virtual environment path: $venv" >&2
  exit 2
fi
(
  umask 022
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install --disable-pip-version-check 'pywebpush==2.5.0'
)
# Repair permissions from older failed attempts that created the venv while
# umask was 077. Symlinks to system Python are not traversed by chmod -R.
chmod -R a+rX "$venv"
"$venv/bin/python" -c 'import pywebpush,cryptography,requests'

# The private key is created exactly once and never stored in Git or logs.
"$venv/bin/python" - <<'PY'
import base64,os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import pwd
private=Path('/var/lib/caesar-manager/push-private.pem')
public=Path('/var/lib/caesar-manager/push-public.key')
user=pwd.getpwnam('caesar-manager')
if private.exists():
    key=serialization.load_pem_private_key(private.read_bytes(),password=None)
else:
    key=ec.generate_private_key(ec.SECP256R1())
    payload=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    fd=os.open(private,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(payload)
raw=key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
value=base64.urlsafe_b64encode(raw).rstrip(b'=')+b'\n'
if public.exists():
    assert public.read_bytes()==value,'Existing VAPID public key does not match private key'
else:
    fd=os.open(public,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(value)
for path in (private,public):
    os.chown(path,user.pw_uid,user.pw_gid)
    os.chmod(path,0o600)
PY

runuser -u caesar-manager -- "$venv/bin/python" "$src/server/hostkey_push.py" check
for name in caesar-hostkey-push.service caesar-hostkey-push.timer; do
  local_unit="/etc/systemd/system/$name"
  source_unit="$src/deploy/$name"
  if [[ -e "$local_unit" ]] && ! cmp -s "$source_unit" "$local_unit"; then
    echo "Existing unit differs: $local_unit; refusing to overwrite" >&2
    exit 2
  fi
  install -m 0644 "$source_unit" "$local_unit"
done
systemctl daemon-reload
systemctl start caesar-hostkey-push.service
systemctl enable --now caesar-hostkey-push.timer
systemctl is-active --quiet caesar-hostkey-push.timer
systemctl is-active --quiet caesar-xray-expiry.timer
echo 'CAESAR_HOSTKEY_PUSH_READY: https://vpn.caesarbot.ru/manager/'
echo 'Expiry blocking: caesar-xray-expiry.timer active (every five minutes)'
echo 'Push delivery: caesar-hostkey-push.timer active (every minute)'
