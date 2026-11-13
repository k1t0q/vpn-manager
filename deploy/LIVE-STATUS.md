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

The existing five already-expired original subscriptions have an initial access hold, explicitly approved during promotion, to avoid mass disconnection. An explicit subscription change releases the hold for that client.

## Rollback

Owner-only code/routing rollback:

```bash
python3 /root/promote-vpn-live.py --rollback /root/vpn-manager-backups/live-promotion-20261113T074312Z
```

It restores previous API routing/services and removes the added 8444 route. It does not restore a stale database, delete current profiles/history, or restart AmneziaWG/Xray. Do not manually overwrite SQLite with an old backup.
The exact rollback command is also stored in /var/lib/vpn-manager-production/rollback-command.txt.

## Remaining verification and limits

Production browser sign-in and final visual checks require the owner to enter the independent production login code. It is stored privately in /root/vpn-manager-live-login-code; never paste it into chat, GitHub or logs.
Notifications currently use the panel and a durable SQLite notification outbox. External Telegram/email/SMS delivery is not configured.
The interface boot unit is enabled and its resolved start command was verified; no VPS reboot or VPN restart was performed. The current running interface was not started by this migration.
Actual test-device connectivity was confirmed by the owner earlier in staging. No external VPN device is available inside this environment for a new production handshake test.
