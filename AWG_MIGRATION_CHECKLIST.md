# AWG migration status

Implemented in the isolated migration branch:
- Original SQLite desktop/mobile UI targets the new API URL.
- Short-lived Flask/SQLite auth; no browser root token.
- Client CRUD, history, renewal, manual referral awards; no expiry-triggered VPN calls.
- Verified-peer link adapter and proxy for explicit AWG operations.
- Duplicate edit route and obsolete wg-easy deletion are excluded.
- Removed retired external-database frontend/backend entrypoints.

Validation: four backend integration tests with 59 synthetic records and mocked
AWG; three recovery parsing tests; JavaScript/Python syntax and diff checks.
Browser rendering could not be tested because Chromium is blocked by the runtime.

Not deployed, not merged. Live client data and profile mappings are not verified.
Linked-profile deletion remains blocked. Multi-step create/link requires inspection
before retrying a failed request. See README for isolated deployment and live gates.
