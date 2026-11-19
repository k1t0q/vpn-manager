# CAESAR VPN — mobile notifications on HOSTKEY

Install after `git pull` on `xray-hostkey-2026-11` and after the existing
`caesar-xray-expiry.timer` has been confirmed active:

```bash
bash /srv/caesar-manager-src/deploy/hostkey-push-stage.sh
systemctl restart caesar-vless-panel caesar-vless-subscriptions
```

The installer creates a private VAPID key under
`/var/lib/caesar-manager/push-private.pem` (mode `0600`, never in Git), a
separate Python virtual environment and isolated systemd send timer. The
admin-panel service keeps its localhost-only network policy. No VPN keys,
subscriptions or Xray/Caddy configuration are modified by the installer.

In the HTTPS administrator panel, click **Включить уведомления** and then
**Проверить push**. Delivery is queued and attempted within one minute.
Alerts are sent once per relevant subscription date and device: during the
7-day, 3-day and 1-day windows, and once after expiration. Renewing a client
resets the notification windows for the new expiry date.

On iPhone, first add `https://vpn.caesarbot.ru/manager/` to the Home Screen
from Safari, then open the installed web app and allow notifications. Android
requires a browser with Web Push support and explicit notification permission.

Read-only validation:

```bash
systemctl is-active caesar-xray-expiry.timer
systemctl is-active caesar-hostkey-push.timer
python3 /srv/caesar-manager-src/deploy/hostkey-expiry-verify.py
systemctl list-timers --all caesar-xray-expiry.timer caesar-hostkey-push.timer
journalctl -u caesar-hostkey-push.service -n 20 --no-pager
```

The expiry timer removes expired *managed* VLESS UUIDs from Xray after the
Moscow paid-through date, with a maximum five-minute scheduled delay.
Unmanaged legacy test UUIDs are preserved by design and must be reviewed
separately before deletion.

If the read-only expiry check reports active keys for expired clients, stop the
push installer, inspect the expiry timer journal, and only then manually
trigger its existing reconciliation service (which may briefly restart Xray).
