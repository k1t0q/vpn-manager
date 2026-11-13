# Client portal v1.79 — prepared, not deployed

Updated 14 November 2026 (Moscow). Production remains v1.78 until a successful guarded activation is verified.

Owner requested personal links and individual codes for all current and future managed clients, subscription dates/days/progress, unchanged configuration download, QR, Android/PC application links, and removal of delivery buttons while retaining historical records.

Prepared source: 2c623be3bb11006979676b714e025ecb0bcc8ca3. Installer: deploy/portalinstall.py (latest branch revision). No production VPN, client DB, profiles, keys, or Caddy changes have been made for this portal rollout.

Eight new local tests pass: client separation, mandatory code, encrypted credentials, stable idempotency/restart/renewal, code reset/revocation/session invalidation, persistent login throttling, disabled/expired/trial/lifetime subscriptions and progress, exact config bytes and QR input, stale/corrupt snapshot rejection, Origin/security headers, and archived/unmapped/symlink profile exclusion. Existing pilot service is preserved as a separate module. JavaScript syntax checks pass. GitHub Actions must pass before activation.

Architecture:
- portal_service.py runs as existing isolated vpn-connect UID, no VPN control token or access to original profiles/main DB.
- A root snapshot reader queries only fixed loopback GET /api/wg/clients for the same labels shown in the manager; no proxy inheritance. It reads client SQLite in mode=ro and copies unchanged profile bytes into the existing protected material directory. The public service never receives this root credential.
- Missing/mismatched labels fail closed rather than assigning an incorrect CAESAR number. Snapshot age above 150 seconds blocks access.
- New tables exist only in the separate encrypted link database; no working client DB migration.
- Codes are 12 random characters, PBKDF2 checked, recoverable copy encrypted with the existing separately stored key. Wrong-code rate limits persist across restart.
- Secure HttpOnly SameSite=Strict session cookies expire in 12 hours; session scope includes client and link handle. Reset code, revoke and rotate invalidate relevant sessions.
- Expired/disabled clients can inspect subscription status after login, but config/QR delivery is blocked.
- Account name is CAESAR VPN #N with N from the existing manager label. No names, dates, secrets or QR appear before authentication.
- New client profiles appear on the next periodic snapshot; link/code issuance is idempotent and never provisions a VPN peer.
- Three main controls: download .conf, obtain QR, transfer file to an application through supported native file sharing. The last is not automatic app launching or VPN activation; unsupported browsers receive an explicit explanation.
- Links for iOS DefaultVPN, Android AmneziaWG and Windows official releases are included. Layout adapts for desktop/mobile.
- Manager: personal-account dialog, separate copy link/code controls, explicit reset/reissue/revoke controls. Delivery buttons removed; previous delivery records untouched.

Deployment gates:
1. Reauthenticate AdminVPS; previous cloud-browser console session expired and returned Unauthorized. Opera Connector also reports Browser not connected.
2. Run read-only deploy/portalaudit.py to verify actual metadata/label mechanism and server identity.
3. Review/run pinned portalinstall.py only after CI success. It backs up current client SQLite using SQLite backup API (WAL aware), existing link DB/key, code/drop-ins, and records profile/config/interface/Xray/bridge invariants.
4. Start the new snapshot and isolated portal backend; test code gate, exact .conf bytes and QR using existing disposable pilot client ID 92.
5. Publish new manager controls only after those checks. Verify original client/history rows, all profile digests, AWG config/runtime peers/interface index, Xray/root bridge PIDs unchanged.
6. No AWG/Xray restart, client reprovisioning, Caddy rerouting or existing profile changes.

Rollback command after activation: python3 /root/vpn-portalinstall.py --rollback (use the actual downloaded pinned path).
Rollback stops portal delivery and its snapshot timer, restores previous code/drop-ins/UI, and preserves current client DB and profiles. It intentionally does not reopen the old code-less pilot endpoint or restore an old client DB.

Remaining: server audit/activation and real device UI/import test. Automatic DefaultVPN deep-link import remains unsupported/unconfirmed; native file sharing is an honest optional manual-import aid, not a substitute claim.
