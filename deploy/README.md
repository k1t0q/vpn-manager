# Isolated staging deployment (not installed yet)

The workflow is gated by repository variable `STAGING_DEPLOY_ENABLED=true` and
environment `vpn-staging`. Keep the variable disabled until bootstrap is approved
and checked. Store `STAGING_SSH_KEY` and `STAGING_HOST_FINGERPRINT` only as environment
secrets. Limit environment deployments to `awg-migration-2026-11`.

The key belongs to `vpn-deploy`, with a root-owned forced command and `restrict`.
It can only deploy the exact migration branch head or restore a previously healthy
preview release. No shell, SFTP, forwarding, production service restart, AWG/Xray
management or dependency installation is allowed through this key.

The root-owned gate never executes downloaded repository code. It rejects archive
traversal, links and devices, rejects dependency changes, switches an isolated
release symlink, restarts only `vpn-manager-stage.service`, checks loopback health,
and restores the previous release if startup fails. The preview runs as the
unprivileged deploy user under systemd filesystem/process isolation. Its SQLite
database is an independent backup; login sessions are not copied. It has no AWG
token. Therefore this first deployment stage cannot control or test real peers.

`prepare_stage.py` is a one-time **root** bootstrap requiring explicit approval
for the new key, account, sudo rule and preview service. It does not read process
environments or copy existing credentials. Preview login remains disabled until
the owner sets a separate login code. It does not modify Caddy or any existing service definition.
Dependency installation and preview service configuration are root-owned.

No bootstrap or key installation has been run. HTTP routing must be installed and
validated separately after a successful loopback deployment. The existing
`/panel-test/` frontend/backend are not replaced by these scripts.

Rollback: dispatch `Validate AWG migration` on the migration branch and enter the
full SHA of a previously healthy preview release in `rollback_sha`. This changes
only the release pointer and preview service. It never restores or overwrites the
working SQLite DB, profiles or configuration. Preview schema changes still require
backward compatibility; this pipeline does not run arbitrary migrations.

Before activation, verify the root-owned key restrictions, sudoers, service sandbox,
remote host-key fingerprint pin, filesystem permissions, both deployment and rollback, and
zero access to the production DB/AWG config from the preview unit.
