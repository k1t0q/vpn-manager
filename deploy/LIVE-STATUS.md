# Live activation — 13 November 2026

Production: https://185-23-239-142.sslip.io:8444/panel-live/
Isolated auto-deployed preview: https://185-23-239-142.sslip.io:8443/panel-stage/

The preview now uses a read-only snapshot bridge. Its GitHub deployment identity cannot update production code, production credentials, the production peer controller, the working database or VPN configuration.

## Installed release

The base production code is frozen at 60601b84b9a957f6c069f13c4ee57f34941a348e. Owner-approved activation completed with deploy/resume.py at d2400d85870aee7b0d5dc3aca3d3e9644552c1fd.
The new HTTPS origin inherits the exact existing 8443 TLS issuer policy. This avoids conflicting automation policies for the same hostname.

The peer controller received a pinned deletion/recovery hotfix from 05ade668c447f1af46dece59c6a5d9fc453e4b9c, installed by deploy/installfix.py at 6e9e4ba864965443a4d4c14206340b17c3441e15.
Deletion first atomically renames the profile inside its own profiles mount. A private durable copy and journal support undo or redo across separate systemd bind mounts. These root modules remain outside automatic staging deployment.

Installed hotfix digests and the private backup path are recorded in /var/lib/vpn-manager-production/engine-hotfix.json.
Do not rerun promotion or recovery scripts on an already active server.

## Verified against the live API

The owner-authorized deploy/verifylive.py smoke test used only newly created disposable profiles. All were archived/deleted after testing; original profiles were never used as mutation targets.

Passed: login and loading all 59 original clients; exact 24-hour trial; atomic provisioning with identical-request retries; configuration and SVG QR generation; disabling, renewing while manually disabled, and enabling; editing; marking/resetting profile delivery; referral awards of 14, 30 and 60 days; expiry disable and automatic restoration after renewal; lifetime renewal preserving an unlimited deadline; history retention; import candidate inspection; deletion and cleanup.

Final report: /root/vpn-live-verification.json.
It confirmed original 59 rows and original client.conf bytes unchanged, all original runtime peers preserved (60 runtime peers, including the pre-existing unmanaged test peer), SQLite integrity OK, no original client archived, no pending journal, Xray PID unchanged. AmneziaWG and Xray were not restarted.

The original promotion initially held access for five already-expired subscriptions. On 13 November the owner explicitly approved releasing those holds: client IDs 36, 37, 38, 45 and 47 were disabled, retaining profiles, keys, addresses and history. Their access can be restored after renewal through the existing automatic reactivation logic.

## Rollback

Owner-only code/routing rollback:

```bash
python3 /root/promote-vpn-live.py --rollback /root/vpn-manager-backups/live-promotion-20261113T074312Z
```

It restores previous API routing/services and removes the added 8444 route. It does not restore a stale database, delete current profiles/history, or restart AmneziaWG/Xray. Do not manually overwrite SQLite with an old backup.
The exact rollback command is also stored in /var/lib/vpn-manager-production/rollback-command.txt.

## Remaining verification and limits

Production browser sign-in and final visual checks require the owner to enter the independent production login code. It is stored privately in /root/vpn-manager-live-login-code; never paste it into chat, GitHub or logs.
Notifications use the panel and a durable SQLite notification outbox. The later Web Push release below adds browser-device delivery; Telegram/email/SMS delivery is not configured.
The interface boot unit is enabled and its resolved start command was verified; no VPS reboot or VPN restart was performed. The current running interface was not started by this migration.
Actual test-device connectivity was confirmed by the owner earlier in staging. No external VPN device is available inside this environment for a new production handshake test.


## Profile labels and download update — 13 November 2026

Installed `sqlite-manager.js`, `index.html`, `awg_provision.py` and `awg_readonly_bridge.py` from 6e0ec7b00e9901c124d8948ed01557cf4a691914 with guarded installer 11549314cd1bd5924b94de0be603d78c957f9683.
Installed the safe profile-name API allowlist from a816083ae1c23c343a960776ec6f85ced4cd2dbb using f175d6ede68a126903fef6c6b51cd41495551d9b.

Customer names remain independent from permanent `CAESAR BOT #N` profile labels. The SQLite `awg_profile_labels` registry reserves unique numbers, including archived records. Renames and retried creation requests retain the label. Existing config/metadata files were not rewritten.

The Config button now downloads a .conf file directly instead of opening native Share. The page is v1.70 and the script URL has a new cache version. Login also trims accidental leading/trailing whitespace from the administrator code.

A fresh inventory before installation found 58 active original clients and original client 57 already explicitly archived. No active profile files were missing. The update preserved the current archive state; it did not restore that client.
A WAL-aware backup is under /root/vpn-manager-backups/profile-labels-20261113T113000Z. A separate profile-name-proxy backup preserves the previous unprivileged API source.

Live verification retrieved all 58 existing configs through the authenticated API and compared every byte with disk. Labels were unique and correctly formatted. A newly created disposable profile retained its number and config after customer rename and an identical creation retry; the test profile was then archived.
The browser downloaded a harmless dummy .conf using the exact configuration-download block extracted from the installed production script. No real client credentials were used in this browser fixture. Interactive production sign-in/full button flow still requires the owner session.
No AmneziaWG/Xray restart, key rotation, client address change or QR payload change occurred.


## Daily expiry checks and Web Push — v1.75

The owner requested subscription expiry checks once per 24 hours. The installed production loop uses an interval of 86400 seconds and also checks on service startup. This can delay expiry disconnection by up to 24 hours, including trial subscriptions. The VPN controller is not restarted by the Web Push installation.

Installed app release: 604b5e41ccf46e0e864757963d9a05d857eee9fc.
Guarded installer: deploy/pushinstall.py at 59614cab27896b34144caaa861c0dd299cf6cf19.
Private backup: /root/vpn-manager-backups/web-push-20261113T164759Z.

Web Push runs as vpn-panel in a separate systemd oneshot service, scheduled by vpn-manager-push.timer every 15 minutes. It reads the current client snapshot through the existing authenticated loopback bridge and never writes VPN settings. It sends reminders in the 7-day, 3-day, 1-day and expired windows; lifetime subscriptions are excluded. Per-device delivery records suppress successful repeat sends, retry failed sends with backoff, deactivate gone endpoints and recognize a renewed deadline as a new reminder cycle. External delivery is encrypted with aes128gcm and a private VAPID key stored only on the VPS and in the private backup.

The panel now retains only the combined “До 7 дней” and “Истекли” groups. It provides authenticated device subscribe/unsubscribe, enable/test/disable controls, a scoped manifest, icons and service worker for /panel-live/. Endpoints are restricted to recognized push providers; redirects are disabled. API tests cover authorization, malformed subscriptions, SSRF rejection, per-device deduplication, expired subscriptions, renewal, retry/backoff, gone endpoints and a real encryption/decryption round trip without network traffic.

Installer verification passed: API login, authenticated public-key response, scheduler health, push worker run, enabled timer, unchanged AWG disk configuration and peer set, unchanged Xray and root bridge PIDs, and preserved daily expiry interval. AmneziaWG and Xray were not restarted. Before activation, dependencies and private-key readability were checked as vpn-panel. Earlier installation attempts rolled back code automatically when these permission checks failed; they did not restore old client databases.

Actual delivery to the owner's iPhone remains to be confirmed. On iOS 16.4 or newer: open the production URL in Safari, use Share → Add to Home Screen, open the installed icon, sign in, tap “Включить уведомления”, allow notifications, then tap “Проверить push”. A successful push-service HTTP response alone does not prove handset display.

Owner-only code rollback:

```bash
python3 /root/vpn-pushinstall.py --rollback /root/vpn-manager-backups/web-push-20261113T164759Z
```

This disables the push timer and restores the previous panel/API code. It retains the current database, VPN state, profile history and device key; never manually restore an old client database to roll back a UI release.
