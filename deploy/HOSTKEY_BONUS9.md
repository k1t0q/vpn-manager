# CAESAR VPN — additional nine compensation days

This is a one-time follow-up to the 19 November 2026 **+14-day** campaign.
Only clients with a matching `compensation_14d` history entry from that
campaign are eligible. Excluded: test accounts #88/#92/#93, archived,
expired and lifetime accounts. Clients created after the original award are
not included.

On HOSTKEY, review the **read-only** plan first:

```bash
python3 /srv/caesar-manager-src/server/hostkey_bonus9.py plan --exclude-test-ids 88,92,93
```

The plan must confirm the original +14-day campaign and show the intended
recipient count with example old/new expiry dates. If it fails or the group
doesn't look right, stop and investigate; do not bypass the checks.

After reviewing the plan, apply once as root:

```bash
python3 /srv/caesar-manager-src/server/hostkey_bonus9.py apply --exclude-test-ids 88,92,93 --confirm-compensation
```

The operation creates a private SQLite backup in
`/root/caesar-compensation-backups`, writes a separate `compensation_9d`
history entry per affected client and a one-time campaign ledger row.
Re-running the apply command becomes a no-op. The script does not restart
Xray or change keys, Caddy or tariffs. The panel reflects updated dates
on its next refresh.
