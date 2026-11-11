#!/usr/bin/env python3
"""Read-only subscription expiry audit for SQLite staging.
No WireGuard requests, no database writes.
"""
import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEFAULT_DB = "/opt/vpn-manager/data/clients-staging.db"

def audit(db_path, today):
    path = Path(db_path)
    if not path.is_file():
        raise SystemExit("Database not found: " + str(path))
    con = sqlite3.connect("file:" + str(path.resolve()) + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT id,name,period,end_date,wg_client_id FROM clients ORDER BY id").fetchall()
    finally:
        con.close()
    counts = {"total": len(rows), "lifetime": 0, "expired": 0, "due_soon": 0, "active": 0, "unknown": 0}
    actions = []
    for row in rows:
        c = dict(row)
        if c["period"] == "lifetime":
            counts["lifetime"] += 1
            continue
        if not c["end_date"]:
            counts["unknown"] += 1
            continue
        try:
            expiry = date.fromisoformat(str(c["end_date"])[:10])
        except ValueError:
            counts["unknown"] += 1
            continue
        days_left = (expiry - today).days
        if days_left <= 0:
            counts["expired"] += 1
            actions.append({"id": c["id"], "name": c["name"], "days_left": days_left,
                            "wireguard_linked": bool(c["wg_client_id"]), "would": "disable_if_enabled"})
        elif days_left <= 3:
            counts["due_soon"] += 1
            actions.append({"id": c["id"], "name": c["name"], "days_left": days_left,
                            "wireguard_linked": bool(c["wg_client_id"]), "would": "notify"})
        else:
            counts["active"] += 1
    return {"mode": "READ_ONLY", "date": today.isoformat(), "counts": counts, "candidates": actions}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--date", help="YYYY-MM-DD (defaults to Moscow calendar date)")
    parser.add_argument("--show-names", action="store_true", help="Include client names in output")
    args = parser.parse_args()
    if args.date:
        today = date.fromisoformat(args.date)
    else:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    result = audit(args.db, today)
    if not args.show_names:
        for c in result["candidates"]:
            c.pop("name", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
