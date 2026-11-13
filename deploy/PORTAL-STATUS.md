# Client portal v1.79 — deployed and server-verified

Date: 14 November 2026 (Moscow). VPS AdminVPS 185.23.239.142.
Manager: https://185-23-239-142.sslip.io:8444/panel-live/
Portal origin: https://connect.caesarbot.ru:8444/

## Delivered

- Personal link and individual 12-character random access code for all 58 current, non-archived managed clients, including existing disposable test client ID 92.
- Manager button “Личный кабинет” opens link/code fields with separate copy controls. Explicit code reset, link reissue and revoke are available inside that dialog.
- “Отметить передачу”/“Передан” action buttons removed. Existing delivery timestamps/history remain untouched.
- Generic page reveals no client identity, subscription details, configuration or QR before authentication.
- After code entry: CAESAR VPN #N, matching the manager's existing CAESAR BOT #N, subscription end date, remaining days and remaining-time progress. Lifetime displays infinity without an end date.
- Three main controls: download unchanged .conf, obtain QR from the same configuration payload, and transfer the profile file through supported native sharing. Unsupported browsers explicitly explain why file transfer is unavailable.
- Official iOS DefaultVPN, Android AmneziaWG and Windows AmneziaWG download links. Responsive desktop/mobile layout.
- Renewals preserve the link and access code; current subscription fields update from the existing database. Newly created managed profiles appear on the periodic snapshot, normally within one minute. No VPN reprovisioning is involved.

## Architecture and security

Public backend runs as existing vpn-connect UID on loopback 5057. It has no AWG management token and no access to original profiles or the working client database.

Root snapshot reader opens client SQLite with mode=ro and reads the existing awg_profile_labels(client_id,label_number) registry inside the same read transaction. This was verified on the actual VPS. It does not use API/VPN credentials, assign new labels, write client SQLite, or modify original metadata/configuration. Migrated wg_client_id values are legacy UUIDs; they are never treated as filesystem paths. Numeric local client ID is checked against metadata.client_id.

Only non-archived, exactly mapped existing profiles are copied to the already protected material directory. Public access fails closed if the snapshot is stale beyond 150 seconds or file hashes mismatch. Expired/disabled clients can inspect subscription status after login but cannot obtain config/QR.

Codes are PBKDF2 checked, with recoverable copies encrypted in the separate link database under the existing separately stored encryption key. Link tokens remain in URL fragments. Secure HttpOnly SameSite=Strict sessions expire after 12 hours. Code reset, revoke and reissue invalidate affected sessions. Persistent wrong-code throttling survives restart. No analytics, public logs, search indexing or configuration payloads in reports.

No schema migration of the working client DB. New code/session tables exist only in the separate link database.

## Verified results

GitHub Actions passed for deployed release and installer:
- Source 4f9e86ce4377fb42ae0c5c3023bbe6bee5b6351f, run 43513888315.
- Installer 0cdafb18fc3ed96f4fdb4c3e3baca949b8477c66, run 43513937699.
- Eight new local security, subscription and snapshot tests passed.

Server installer reported:
- 58 personal accounts and unique link handles, codes available.
- Unauthenticated config request rejected with 401.
- HTTPS login with code succeeds for the disposable pilot.
- Downloaded .conf bytes exactly equal the existing pilot client.conf.
- QR endpoint succeeds.
- All pre-existing client.conf hashes unchanged.
- Working clients and client_history rows equal the WAL-safe backup.
- AWG config, runtime peer list and interface index unchanged.
- Xray and root bridge PIDs unchanged; neither AWG nor Xray restarted.

Additional deploy/portalverify.py check passed through the actual authorized manager API:
- all-client portal capability and codes available to administrator;
- legacy client's public number matches the existing registry;
- 58 accounts reported;
- public backend and periodic snapshot timer active;
- observed snapshot age 22 seconds.

Cloud browser displayed portal login with Android/PC/iOS links and manager v1.79 over trusted HTTPS. A real physical device login/import test remains pending; no automatic DefaultVPN launching or VPN activation is claimed.

## Backup and rollback

Private backup: /root/vpn-manager-backups/portal-20261113T214612Z.
Contains WAL-aware client SQLite backup, link DB before/after activation, encryption key and previous code/drop-ins. No private values are included here.

Code rollback:
python3 /root/vpn-portalinstall.py --rollback

It stops public portal delivery and its snapshot timer, restores previous code/UI/drop-ins and preserves the CURRENT client DB and VPN profiles. It intentionally does not reopen the earlier code-less pilot endpoint and never restores an old client DB over live data.

The first guarded attempt stopped before public activation because migrated UUID links were incorrectly excluded. Old UI was restored, no VPN/client data changed. The reader and its regression test were corrected to use verified numeric local IDs, after which activation passed. No original UUID links were changed.

## Remaining limitation

“Передать в приложение” uses the operating system's supported file-sharing chooser. It is a manual import aid. Direct browser → DefaultVPN import of a custom AmneziaWG profile remains unsupported/unconfirmed; the site cannot enable the device VPN on its own. Existing .conf and QR methods remain available.

Recommended owner smoke test: refresh manager → client → Личный кабинет → copy link and code → open the complete link on phone or PC → enter code → verify displayed number/subscription → download .conf or scan QR → import/connect in the chosen application. Do not publish personal URLs, codes, QR or configs.
