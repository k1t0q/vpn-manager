# AdminVPS live audit — 2026-11-12

Server identity verified in the AdminVPS control panel and guest: 185.23.239.142.
No Timeweb servers were accessed. Working AWG and Xray were not restarted.

## Verified live

- SQLite: 59 clients and 8 existing history records.
- Metadata: 59 profiles; all metadata public keys and client addresses match the 59 disk peer blocks.
- All 59 managed public keys are present in the running awg-test interface.
- Running interface has 60 peers; its single extra public key matches the existing iphone-test profile.
- vpn-manager is active.
- awg-quick@awg-test is enabled but inactive, Result=success. The stock unit runs /usr/bin/awg-quick up %i. This does NOT prove reboot recovery; the running interface was not restarted.
- Actual awg_adapter.py blocks create and toggle with 409 and maps profile IDs from numeric clients.id. The legacy wg_client_id mapping is not verified and must not be used as an authoritative AWG ID.
- Working and test services exist; API route ownership was inspected.
- Full background-job and notification audit remains outstanding.

## Backups

Protected server-local directory:
`/root/vpn-manager-backups/20261112T170953Z`

Includes a SQLite backup made with sqlite3.Connection.backup (WAL-aware), integrity_check=ok, 59 copied clients, and a protected tar archive of application code, profiles, AWG/Caddy/systemd/cron configuration and test frontend. No keys/config contents were published.

Do not restore the database over current data as a routine code rollback. Preserve all subsequent client/history changes. Any restoration of production configuration needs explicit approval.

## Prepared code — not connected to production API

- server/awg_persistence.py: atomic config/metadata writes, file locking, protected client IDs, request idempotency, private per-operation backups, a durable undo journal, SQLite commit markers, and explicit online/offline recovery.
- Uses per-peer awg set; never syncconf or interface restart. Unknown runtime peers remain untouched.
- Boot recovery requires installing and validating an ordering hook BEFORE any live use. No hook was installed.
- Creation, deletion, automatic expiry/re-enable and notifications are NOT implemented by this module.
- Removed the UI history button in source; history routes and records are unchanged. This UI change is not deployed to panel-test.

## Validation

Commit c3208ea7a6745eadccdd264b71ef4ed4319f485e:
- 12 Python tests pass locally and on this VPS against synthetic temporary databases and a fake AWG runtime.
- 3 JavaScript tests and JS syntax checks pass locally and in GitHub Actions.
- GitHub Actions run 43267939057: success.
- Source unpacked under /opt/vpn-manager-review for isolated tests; existing application directories/services were not replaced.
- No real profile was created, deleted, toggled or regenerated.

## Remaining gates

The task is not complete. No automatic staging deployment has been configured and no SSH deployment key has been installed. Browser GitHub authentication was not completed, so Actions Secrets could not be configured. The connector does not support Secrets APIs.

Next: finish numeric ID validation, audit scheduled jobs and test panel differences, implement creation/deletion/subscriptions/notifications with error tests, prepare the constrained staging deployment account and release/rollback scripts, configure secrets securely, then obtain explicit approval before activating changes to the working VPN. Verify with a new disposable profile, not any of the 59 existing profiles or the existing iPhone test.
