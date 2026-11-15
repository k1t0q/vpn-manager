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

## Compact layout update — 14 November 2026

Deployed static assets b45897cf23e6db5e6a2f894402f1fbad91fd6694 using deploy/portalcompact.py at 322f8318e3b9f023727114de0c9f07c87869c26e. GitHub Actions run 43515745479 passed; JavaScript syntax check passed. HTTPS HTML/CSS and browser appearance verified.

Main block reduced from 790 to 520 px, smaller gaps/padding, simplified subscription display/progress, two primary action buttons side by side, compact application links, instructions remain collapsed.

The native “Передать в приложение” file-sharing control was removed because it did not meet the owner's requested direct app import. This supersedes the earlier three-control description. Current controls are .conf download and QR. Credentials/subscription backend and all client data remain unchanged. No services restarted. Backup: /root/vpn-manager-backups/portal-compact-20261113T220823Z; files.json maps the three original assets saved as 0/1/2.

Further research found an explicit raw WireGuard/AmneziaWG deep-import format in INCY's own documentation: https://github.com/INCY-DEV/incy-docs/blob/main/ru/dev-docs/deep-links.en.md (incy://import/{base64-conf}). It is a candidate for an owner-approved separate test, NOT an implemented or physically verified integration. No configurations have been sent to INCY, no application changed, no experimental button published. DefaultVPN direct import remains unconfirmed. A universal reliable browser check of arbitrary app installation must not be simulated with a timer.

## v1.80 — aligned controls and mobile-only INCY experiment

Deployed 14 November 2026 (Moscow).
Source: be5ea53ad87ae2cba327d52b3247d30cf9daef8d.
Installer: deploy/uiuniform.py at 6e7647f3d5ad47ed90b8f33fbe0f7483a18c41af.
GitHub Actions runs 43517624979 and 43517684338 passed. New local checks: nine Python portal/snapshot tests and two Node INCY import-contract/platform tests; both JavaScript syntax checks passed.

Manager layout:
- Desktop toolbar moved to the right side of the header, visible only after manager login. Dynamic Import Profiles insertion still follows Export CSV in that same toolbar.
- Equal toolbar button dimensions, consistent gaps and press/hover/focus feedback.
- Table header/value alignment and equal-height action grid.
- Personal-link dialog and public account spacing normalized; operation indicators preserve button dimensions.
- Mobile header remains separate; mobile import remains hidden.

INCY:
- Owner confirmed manual configuration compatibility, then reported that their Windows build says AmneziaWG is supported only on Linux.
- No protocol/server/profile changes were made to work around an application limitation. Windows remains on AmneziaWG.
- Direct-import button is available ONLY for existing disposable client ID 92, and only on mobile browsers (including iPad desktop user agent). It is hidden on Windows/macOS/Linux desktop.
- Backend exposes a pilot flag only after authenticated profile access. Normal clients do not receive the experimental control.
- For that one pilot, the authenticated page prepares an unchanged config in browser memory. Clicking uses the documented local URI incy://import/{base64-conf} synchronously, without a file-sharing chooser. No config URI is generated in server logs, GitHub, reports or analytics.
- The encoding test verifies exact byte-for-byte round trip, including UTF-8 comments. Config/QR downloads remain available.
- A conditional fallback offers install/update guidance if INCY has not opened. It does NOT claim the browser has proven the app is absent.
- Documentation: https://docs.incy.cc/deep-links/.
- Real mobile URI import/connection is PENDING. Manual file compatibility is not treated as proof of deep-link success. Do not roll out the control to all clients until that test succeeds.

Server installer verified authenticated pilot flag true for ID 92 and false for an ordinary client, exact pilot configuration bytes, manager v1.80/header structure, unchanged client/history rows and original profile/config hashes, unchanged Xray/root bridge PIDs. Only the unprivileged public portal backend was restarted; VPN and root bridge were not restarted.

Backup: /root/vpn-manager-backups/ui-uniform-20261113T221612Z.
files.json maps six previous code/assets to numbered backup files. It also contains WAL-safe copies of working client SQLite and link SQLite. A code rollback restores only those six code/assets and restarts vpn-manager-connect.service; do not restore old client SQLite over current data.

Cloud browser observed manager v1.80, confirmed desktop toolbar is inside header with one reload ID, and displayed the updated portal. Full signed-in layout/device smoke test remains with owner; no credentials were exposed to obtain a screenshot.
