# Clash Mi pilot — deployed, physical device test pending

Deployment verified on 14 November 2026 (Moscow). Frontend buttons and official Clash Mi download links are live. Direct import is enabled ONLY for disposable database client 92, using CONNECT_CLASH_PILOT=1 in the scoped portal service. Other clients retain normal .conf and QR access. No successful physical Clash Mi import/connection has yet been observed.

Verified release evidence:
- Button frontend source: 2f90a862d93ec6d15d64596ead1bd6dbaa68a42a; installer 62380732d56abd8530eedf1218aed71e1114d427.
- Clash Mi portal source: 2c397c96f251ae626a811521ebc290681c7bea3f; corrected guarded installer ebcb3a9bda00b68265ba4af56383de4994000eda.
- GitHub validation passed: runs 43530747732 and 43531350527.
- Frontend backup: /root/vpn-manager-backups/buttons-20261114T000213Z.
- Portal backup: /root/vpn-manager-backups/clash-mi-20261114T000324Z.
- Authenticated HTTPS pilot export, original .conf equality, QR availability, original private/public key and client IP preservation, logout invalidation, and non-pilot rejection passed on VPS.
- 58 current clients were present in the portal snapshot; working client rows/history, original profile/config hashes, running AWG peer list and Xray/control-bridge PIDs were unchanged. This verifies preservation across deployment, not the connectivity of every end-user device.
- Only the unprivileged portal backend was restarted; Caddy received a graceful logging-only reload. Routing/listeners were checked unchanged. AWG and Xray were not restarted.
- Caddy default logger now redacts the temporary ticket query parameter; gunicorn access logging is off.
- Initial installer stopped before backup/mutation because Caddy generated file_server.hide entries in a different order/source filename. The corrected comparison normalizes only this generated hide-set field and retains strict guards for other routing fields.
- Copy buttons now show a green confirmation and an in-dialog status message; portal controls share dimensions, spacing and press feedback.

Implemented behavior:
- All three portal installation links now point to official Clash Mi iOS (Russian App Store), Android and Windows downloads.
- Raw .conf and QR are retained byte-for-byte for compatible AmneziaWG applications. They are NOT described as Clash YAML.
- A strict, read-only AWG v1/v2 converter preserves keys, addresses, endpoint, supported obfuscation options, DNS, MTU and keepalive. Unsupported options, additional peers/hooks or split routes fail closed. No server or client profile is rewritten.
- Direct import is disabled by default. CONNECT_CLASH_PILOT=1 enables only disposable client 92. All other clients retain download/QR only.
- URL scheme: clashmi://install-config?url=<encoded temporary HTTPS export>&name=<encoded label>&overwrite=no . No permanent portal credentials are passed to the application.
- Temporary export tickets are minted only after portal link + code/session authentication, stored hashed in separate portal state, valid 120 seconds and at most three GETs. HEAD does not consume a use. Reissuing, logout, session expiration, resetting code, rotating/revoking link, disabled/expired/removed profile, stale snapshot and profile hash changes deny export. There are no remote providers, analytics or external converter services.

Local validation: 30 Python tests plus 2 Node UI tests pass; JavaScript syntax checked. Full repository Actions must pass before release.

Release procedure / requirements before wider rollout:
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

Rollback: use deploy/clashrollback.py from commit a4f69c2a0c648c4bf7d78738809d73ef340b98cf against /root/vpn-manager-backups/clash-mi-20261114T000324Z. This restores only the four portal files, Caddy configuration and scoped pilot environment drop-in, then gracefully reloads Caddy and restarts the portal backend. It retains current databases, profiles, AWG and Xray. Frontend-only button rollback uses the numbered files and files.json in /root/vpn-manager-backups/buttons-20261114T000213Z. Never restore the old working client database or VPN configuration for a code rollback.
