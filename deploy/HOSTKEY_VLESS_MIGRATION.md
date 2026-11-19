# CAESAR VPN — staged HOSTKEY migration (19 November 2026)

This branch is **not** the final production panel. It provides the guarded
VLESS/WS controller, a read-only subscription endpoint and a personal landing
page for a small pilot. The existing AWG-oriented VPN MANAGER UI and API must
be adapted and separately tested before mass cutover.

## Verified source/target inventory

- AdminVPS client SQLite copied by `.backup`, verified `PRAGMA integrity_check=ok`.
- Exactly 70 historic client records and their history, referrals and dates.
- HOSTKEY 193.107.211.46, Xray service active, Caddy service active.
- Xray configuration: `/usr/local/etc/xray/config.json`.
- VLESS inbound: `vless-ws`, `127.0.0.1:10000`, `ws`, security `none`, 2
  pre-existing pilot UUIDs. Caddy terminates TLS on 443 and proxies `/vless-ws`.
- Caddy configuration: `/etc/caddy/Caddyfile` (must be merged, not replaced).
- Live Xray/Caddy backup created on HOSTKEY before this branch was prepared.

## Guardrails

1. Do **not** point any live or new production subscriptions to this service
   until an owner-approved one-client pilot succeeds on UXWay and INCY.
2. The two existing VLESS test keys are unmanaged and always preserved by the
   reconciler. Never delete them during the pilot.
3. This code does not alter `clients`, `client_history` or `wg_client_id`.
   It adds `caesar_vless_devices` and `caesar_connect_links` to a *working copy*
   of the original 70-client SQLite database.
4. Use two separate per-device UUIDs; one permanent personal landing URL.
   Random tokens are credentials: never commit or paste them into public logs.
5. Legacy expired/unknown-date clients are not activated automatically; review
   them and their billing status before cutover.
6. The default `sync` is a dry run: it creates a temporary JSON and asks Xray
   to validate it. Only `sync --apply` alters Xray and briefly restarts it.
   A pre-change config backup and rollback are built in.
7. Expiry/renewal reconciliation and the existing VPN MANAGER UI integration
   still need wiring and tests. Do **not** bulk provision 70 records yet.

## Stage on HOSTKEY (does not modify live VPN)

Run commands as root on HOSTKEY after inspecting the branch and its changes:

```bash
git clone --single-branch --branch xray-hostkey-2026-11 https://github.com/k1t0q/vpn-manager.git /srv/caesar-manager-src
bash /srv/caesar-manager-src/deploy/hostkey-stage.sh /root/caesar-clients-20261119-083859.db
python3 /srv/caesar-manager-src/server/xray_hostkey.py inventory
```

This only creates `/var/lib/caesar-manager/clients.db` and a dedicated local
service user. It aborts if the working DB already exists rather than replacing
client changes.

## Pilot plan (not for bulk execution)

1. Inspect billing dates of the selected test account, check that it is active.
2. Register exactly one existing client ID by running `provision --ids ID`
   against the working DB; this does not change Xray.
3. Run `sync` without `--apply` and review changes/dry-run result.
4. After explicit approval and a brief-restart warning, run `sync --apply`.
5. Install the read-only subscription service and merge Caddy routes.
   Keep `/vless-ws`, the two old test UUIDs and current test pages intact.
6. Verify one permanent landing URL and two distinct tokens from outside the
   server on both Wi-Fi and mobile data. INCY deep-link uses its official
   documented `incy://import/https://...`; UXWay uses the owner-tested
   `uxway://add/https://...`. Both still require real client verification.
7. Adapt admin CRUD, subscriptions, scheduled expiry, renewal, device alerts,
   and bulk onboarding; regression-test before serving the other clients.

## Tests

```bash
python3 -m unittest discover -s tests -p 'xray_*_test.py' -v
```

No real IP, UUID, token, password, private key or customer data is hardcoded
in this branch.
