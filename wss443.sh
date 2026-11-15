#!/usr/bin/env bash
set -euo pipefail
CONFIG=/usr/local/etc/xray/config.json
CERT=/etc/letsencrypt/live/vpn.caesarbot.ru/fullchain.pem
KEY=/etc/letsencrypt/live/vpn.caesarbot.ru/privkey.pem
BACKUP=/root/xray-before-wss-$(date +%Y%m%d-%H%M%S).json
test -s "$CONFIG"
test -s "$CERT"
test -s "$KEY"
cp -a "$CONFIG" "$BACKUP"
cat > /root/xray-wss-candidate.json <<'JSON'
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "listen": "0.0.0.0",
    "port": 443,
    "protocol": "vless",
    "settings": {
      "clients": [{"id": "b439edd9-7013-4900-a32b-f7069ac4c69a"}],
      "decryption": "none"
    },
    "streamSettings": {
      "network": "ws",
      "security": "tls",
      "tlsSettings": {
        "certificates": [{
          "certificateFile": "/etc/letsencrypt/live/vpn.caesarbot.ru/fullchain.pem",
          "keyFile": "/etc/letsencrypt/live/vpn.caesarbot.ru/privkey.pem"
        }]
      },
      "wsSettings": {"path": "/vless-ws"}
    }
  }],
  "outbounds": [{"protocol": "freedom"}]
}
JSON
xray run -test -config /root/xray-wss-candidate.json
cp /root/xray-wss-candidate.json "$CONFIG"
if ! systemctl restart xray; then
  cp "$BACKUP" "$CONFIG"
  systemctl restart xray
  echo "FAILED; restored $BACKUP"
  exit 1
fi
if ! systemctl is-active --quiet xray; then
  cp "$BACKUP" "$CONFIG"
  systemctl restart xray
  echo "FAILED; restored $BACKUP"
  exit 1
fi
echo "WSS 443 ACTIVE; backup: $BACKUP"
