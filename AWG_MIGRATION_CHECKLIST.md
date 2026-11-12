# AmneziaWG migration — compatibility checklist (draft)

This branch is isolated from production `main`. Do not deploy it before end-to-end testing.

## Verified from current frontend

- `app.js` hardcodes `BACKEND = "https://80-90-179-65.sslip.io"` (obsolete).
- Frontend login and client CRUD use Supabase Auth and `sb.from("clients")`, `sb.from("client_history")`.
- WG API uses `X-VPN-Token` stored in `localStorage`.
- Existing `wg_client_id` refers to old wg-easy IDs, not necessarily AWG public keys or SQLite client IDs.
- `app.js` automatically calls enable/disable based on remaining days in the edit handler; remove that coupling per operator requirement.
- The backend is reachable at `https://185-23-239-142.sslip.io:8443/health` and reported `5-awg` on 2026-11-12.
- 59 AWG configs were reported present; one new profile (`me`, SQLite ID 33) was successfully tested.

## Before changing frontend

1. Obtain a sanitized listing of actual backend routes and JSON response shapes for clients, history, referrals, session/login, and WG operations. Do not print tokens or client personal data.
2. Confirm which endpoint returns client rows and which identifier the WG API expects. Never assume old `wg_client_id` is an AWG key.
3. Implement server-side admin authentication with short-lived session and strict CORS to GitHub Pages. Avoid embedding the server API token in a public static repository or asking browsers to store a long-lived root token.
4. Replace Supabase Auth and direct Supabase CRUD with corresponding authenticated backend routes, including history and referrals.
5. Remove expiry-triggered AWG disable/enable, while retaining subscription display and warnings.
6. Gate dangerous operations (create/delete/enable/disable) until backend persists both SQLite and AWG config atomically or with recovery.
7. Test on preview branch, then merge to `main` only after verification. Keep production rollback available.

## Security

Never commit `.env`, client configs, private keys, API tokens, QR images, database files, or identifiable client data to GitHub Pages.
