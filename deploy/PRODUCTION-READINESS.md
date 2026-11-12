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

## Blocking evidence before an activation package can be finalized

- Identify the service/process listening on 5052 and any old API writer on 5050.
  The current production app has a catch-all proxy route, so stopping only the
  named main API may leave the actual legacy database writer running.
- Review the new sanitized `legacy_api_writers` and `awg_boot_evidence` audit
  fields. Do not guess service names or stop unrelated Python processes.
- Clean up disposable live staging profiles after the successful device test.
- Freeze production code/assets outside `/srv/vpn-manager-stage`, back up the
  working SQLite with the backup API and preserve all profile/config material.
- Stop and prevent the old staging writer from restarting before allowing a
  production writer. Independent state directories must never concurrently
  rewrite the same AWG configuration.
- Finish and review a pinned owner-run installer and code-only rollback. The
  activation record must be root-owned/private and must require explicit owner
  approval. No current workflow creates that record.

The draft production entry point refuses startup without an approved private
activation record, with an active staging writer, or if baseline key/IP/runtime
invariants fail. It does not start an AWG interface or restart AWG/Xray.

External push/email/Telegram delivery is not configured. Current notifications
are panel alerts and a durable notification outbox. Interface autostart is
enabled in systemd, but the origin of the currently manually-owned interface
has not been established and a reboot has not been performed.

Validation: 72 Python tests and 3 JavaScript tests, including legacy ID mapping,
mixed historic owners, exact trial referral deadlines, import crash/replay,
delivery deduplication, initial expiry holds and irreversible hold consumption.
