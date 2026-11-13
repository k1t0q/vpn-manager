# Clash Mi pilot — prepared, NOT deployed

The production portal remains v1.80. AdminVPS noVNC disconnected and returned Unauthorized after one reload on 2026-11-13 UTC. No server files, profiles, client DB or running VPN changed in this task.

Prepared changes:
- All three portal installation links now point to official Clash Mi iOS (Russian App Store), Android and Windows downloads.
- Raw .conf and QR are retained byte-for-byte for compatible AmneziaWG applications. They are NOT described as Clash YAML.
- A strict, read-only AWG v1/v2 converter preserves keys, addresses, endpoint, supported obfuscation options, DNS, MTU and keepalive. Unsupported options, additional peers/hooks or split routes fail closed. No server or client profile is rewritten.
- Direct import is disabled by default. CONNECT_CLASH_PILOT=1 enables only disposable client 92. All other clients retain download/QR only.
- URL scheme: clashmi://install-config?url=<encoded temporary HTTPS export>&name=<encoded label>&overwrite=no . No permanent portal credentials are passed to the application.
- Temporary export tickets are minted only after portal link + code/session authentication, stored hashed in separate portal state, valid 120 seconds and at most three GETs. HEAD does not consume a use. Reissuing, logout, session expiration, resetting code, rotating/revoking link, disabled/expired/removed profile, stale snapshot and profile hash changes deny export. There are no remote providers, analytics or external converter services.

Local validation: 30 Python tests plus 2 Node UI tests pass; JavaScript syntax checked. Full repository Actions must pass before release.

Before installation:
1. Reopen authenticated AdminVPS root console. Verify server IP, awg-test and existing production service paths.
2. Inspect actual pilot profile OPTION NAMES only (never print values) and run converter privately against original material. Verify exported values against original in memory, without outputting keys/config.
3. Inspect effective Caddy and gunicorn logging. Export URLs contain short-lived bearer tickets: do not enable the pilot until request URL/query logging is absent or redacted, including errors. Do not reduce unrelated security logging.
4. Take WAL-aware client DB and portal DB backups, preserve portal key, code files and relevant service config. Install pinned files atomically with a rollback manifest. Restart only vpn-manager-connect.service, never AWG/Xray/control bridge.
5. Verify original client rows/history/config hashes and Xray/bridge PIDs unchanged. Check HTTPS auth, .conf/QR, pilot gating and ticket lifecycle privately without outputting URLs/codes.
6. Physical test in current Clash Mi on mobile and Windows: open from browser, confirm import, enable VPN, verify Internet and handshake. Actual auto-import/connection is NOT yet confirmed. Do not enable for all clients until this passes.

References (official):
- https://clashmi.app/download
- https://apps.apple.com/ru/app/clash-mi/id6744321968
- https://github.com/KaringX/clashmi/issues/507
- https://wiki.metacubex.one/en/config/proxies/wg/

Rollback: until deployed nothing to roll back. After installation restore only backed-up code and scoped portal service drop-in; restart portal service. Do not restore an old working client DB or VPN configuration.
