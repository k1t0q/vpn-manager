# VPN MANAGER k1t0 — SQLite / AmneziaWG

The original desktop/mobile panel uses `sqlite-manager.js` and `styles.css`.
`config.js` contains only the public HTTPS API URL. No external database service is required.

## Isolated backend

`server/staging_api.py` is a **new panel service**, not a replacement for the running
5-awg service on the VPS. It reads the existing SQLite schema and proxies explicit
AWG actions to that service. Never overwrite `/opt/vpn-manager/app.py` with the
repository launcher: the running VPS app contains the working AWG implementation.

Install this branch into a separate directory and run, for example:

```sh
python -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/gunicorn --workers 1 --bind 127.0.0.1:5052 --chdir server staging_api:app
```

Set these server-only environment variables using a protected environment file:

- `VPN_MANAGER_DB`: absolute SQLite path. First use a SQLite backup copy, never a file copy of an active WAL database.
- `PANEL_ADMIN_CODE`: new long random admin login code.
- `PANEL_AWG_URL`: actual loopback address/port of the existing 5-awg service. The default is only a placeholder and must be verified.
- `PANEL_AWG_TOKEN`: existing server API token, sent upstream as `X-VPN-Token`.
- `PANEL_ORIGIN`: `https://k1t0q.github.io`.

The panel needs write permission to the selected DB and its directory. No tables or
profiles are changed on import/startup. A separate `panel_sessions` table is created
on first successful login; only SHA-256 session hashes are persisted. Sessions expire
after 12 hours. The browser stores its session in sessionStorage, never the upstream token.
One Gunicorn worker is required for the in-process login rate limit.

## Route ownership

`staging_api.py` owns auth, client listing, CORS and the AWG proxy.
`sqlite_client_crud.py` owns create, PATCH, renewal, history and guarded deletion.
`sqlite_referral_safe.py` owns idempotent manual bonuses.
`sqlite_wg_link.py` verifies the profile against the AWG list before linking it.
Do not register `sqlite_client_edit.py` or `sqlite_client_delete.py` alongside these:
the former duplicates PATCH and the latter calls obsolete wg-easy endpoints.

## Remaining live-server gates

SSH was unreachable from the implementation environment. Actual server modules,
schema, AWG identifier format and response bodies have not been inspected. The HTTP
API responds 401 without credentials. Local tests use synthetic clients and mocked
AWG responses; they do not establish that the 59 real profiles are correctly linked.

Before routing traffic to the panel service:

1. Inspect the actual 5-awg routes, loopback port, schema and peer identifiers without printing secrets. Compare each SQLite link with the existing AWG list. Do not regenerate IDs or keys.
2. Run against a backup SQLite database and the running AWG read endpoints; verify all 59 clients, history, names, dates and QR/config formats. Creating a test profile is an explicit write, not part of a read-only smoke check.
3. Test create/link/renew/referral with one disposable test client. Creation remains a multi-step operation: if a response is lost, inspect the existing client/profile before retrying. No automatic rollback deletes VPN profiles.
4. Linked-client deletion is deliberately blocked with HTTP 409. Implement and test an AWG-aware deletion adapter before enabling it. Unlinked test records can be deleted.
5. Only after these checks, point the existing **8443** Caddy API handler to the panel on 5052. Preserve the old target for rollback. Do not edit the 443 listener, Xray service or AWG configuration.
6. Test the preview frontend against 8443, then prepare/merge PR #1. This branch has not been deployed or merged.

No expiry scheduler exists in this panel; changing subscription dates never enables
or disables VPN. Audit existing VPS timers separately before claiming automatic
disabling is absent from the whole host.

## Checks

```sh
python -m unittest discover -s tests -p '*_test.py' -v
node --test tests/recovery.test.mjs
node --check sqlite-manager.js
```
