# Isolated staging deployment

Installed test panel: https://185-23-239-142.sslip.io:8443/panel-stage/

GitHub Actions runs Python and JavaScript tests before deploying migration branch changes to the isolated frontend/backend. Environment `vpn-staging` contains the deployment key and pinned host fingerprint. The root-owned forced SSH command accepts only deployment of the exact migration branch head or rollback to a previously healthy release. It provides no general shell, SFTP, forwarding, production service restart, dependency installation or privileged module update.

The staging service runs as `vpn-deploy` on loopback 5053 under `/srv/vpn-manager-stage`. Releases are immutable SHA directories with an atomic `current` symlink. The gate restarts only `vpn-manager-stage.service`, checks loopback health and restores the previous release if startup fails. Dependencies and unit configuration are root-owned. Public assets are copied through an allowlist. The production `/panel-test/` and main service remain separate.

A frozen root-owned test bridge on loopback 5054 owns a separate SQLite clone and private test profiles. Its immutable baseline protects all 59 original client IDs and original disk/runtime peers. Only new disposable `STAGING TEST` profiles may be changed; at most three may be active. Mutations use per-peer commands, private journal backups, file locking, atomic writes and SQLite backup API. No AWG or Xray restart is used. The automatic pipeline cannot update this privileged code.

Verified on the VPS: profile create/QR/toggle/delete, detail editing, 1/6/12 month renewal, lifetime preservation, exact 24-hour trial, +14/+30/+60 referral awards, automatic expiry/reactivation, manual disable surviving renewal, downloaded CSV with 59 distinct IDs, and preserved deletion history. Test profiles were cleaned up. External notifications are not configured; notifications currently use the panel only. Moscow billing calendar changes require the separately pinned owner-run `upgrade_test_billing_calendar.py` update. Original production adapter locks remain enabled. Orphan import, actual device handshake/mobile tests and complete boot-path verification remain outstanding.

## Rollback

Dispatch `Validate AWG migration` on `awg-migration-2026-11` with the full SHA of a previously healthy release in `rollback_sha`. This changes the isolated release pointer and restarts the staging backend only. This rollback path has been exercised. Never restore an old database over newer client records or history during code rollback.

Privileged owner-run upgrade scripts print their private backup directory. Roll back their code only from the matching backup after checking journal state; never blindly restore an old AWG configuration or SQLite database. Production promotion requires a separately reviewed deployment and explicit owner confirmation, including a decision about the five already expired original subscriptions that are still enabled.
