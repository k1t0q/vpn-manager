# Production preparation — not activated

The current live panel remains the isolated `/panel-stage/` deployment. Its
frozen root bridge still protects all original 59 IDs. GitHub Actions does not
install or start `awg_production_server.py` or change production configuration.

The prepared production controller supports existing numeric billing IDs and
legacy UUID VPN links without changing their keys, addresses, profile files or
historic owners. The same journaled engines handle mutations. Existing unmanaged
runtime peers remain protected. Database-only import links an existing trusted
profile without rewriting it. Delivery marks record an administrator action and
timestamp; a handshake does not imply delivery. Both records retain history.

At activation, the five already-expired original subscriptions must be included
in a frozen `initial_expiry_hold` map. Their access remains enabled on the first
scheduler run and during contact-only edits. A subscription change consumes the
hold permanently. Normal future expiration/reactivation remains automatic.

## Reviewed promotion package — owner activation pending

- The 5050 service is `vpn-manager.service`; the 5052 service is
  `vpn-manager-panel-test.service`. The staging writer is
  `vpn-manager-awg-readonly.service`. All three must be stopped/gated before
  production writes are enabled. No other process is killed by the installer.
- The disposable device-test client has been archived. A fresh audit confirms
  59 original clients, 59 disk peers, all 60 original runtime peers, intact
  production history (8 records), and no pending operation.
- `promote_live.py` defaults to checks only. `--activate-approved` is required
  after the owner separately approves production activation. It verifies pinned
  source digests, original source identities, the database/profile baseline,
  five expiry holds, ports, old API dependencies and potential cron/timer/SQLite
  writers. An unexpected condition stops promotion for review.
- Production code/assets are root-owned and frozen outside the staging release
  directory. The live API runs as a new `vpn-panel` user with a separate session
  database and frozen Python environment; `vpn-deploy` and its SSH key receive no
  production access. A fresh bridge token is stored in private root environment
  files. A separate random production administrator code is stored only in
  `/root/vpn-manager-live-login-code` and the private production environment. It
  is never printed by the script or shared with the staging backend.
- The owner-run script creates WAL-aware backups before and after freezing the
  legacy writers, plus private profile/config/API archives. It never overwrites
  the working SQLite, creates client profiles, or rotates existing keys during
  promotion. It requires unchanged VPN config/runtime peers before and after
  activation. New Caddy routes preserve all old listeners and add only TCP 8444.
  The Xray main PID is checked before/after. An existing restricted 8444 firewall
  rule requires review; if UFW is active and has no 8444 rule, the approved TCP
  rule is added and recorded for rollback.
- Planned live routes are `/panel-live/` and `/live-api/` on a separate HTTPS
  origin at port 8444. Independent credentials and session storage prevent
  automatically deployed staging code from inheriting production authority.
  The test panel remains on 8443 through a new read-only snapshot service on
  loopback 5054. Automatic staging deployments remain isolated from production.
- The exact code-only rollback command is printed with the private backup path.
  It restores the previous API routing and service state, never restores an old
  database/profile/AWG configuration, and never invokes an AWG/Xray lifecycle
  command. On activation failure this rollback is attempted automatically.

The draft production entry point refuses startup without an approved private
activation record, with an active staging writer, or if baseline key/IP/runtime
invariants fail. It does not start an AWG interface or restart AWG/Xray.

External push/email/Telegram delivery is not configured. Current notifications
are panel alerts and a durable notification outbox. Interface autostart is
enabled in systemd, but the origin of the currently manually-owned interface
has not been established and a reboot has not been performed.

Validation: 81 Python tests and 3 JavaScript tests, including legacy ID mapping,
mixed historic owners, exact trial referral deadlines, import crash/replay,
delivery deduplication, initial expiry holds, irreversible hold consumption,
scheduler readiness, snapshot write rejection, canonical deletion and code-only
rollback boundaries. The new production services cannot be checked live until
the owner approves and runs the promotion package. No current workflow deploys
or activates them.
