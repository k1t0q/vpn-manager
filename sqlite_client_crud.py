"""SQLite CRUD routes for VPN MANAGER staging. No WireGuard mutations."""
from datetime import date
import sqlite3
from flask import request, jsonify

FIELDS = {"name", "phone", "start_date", "end_date", "period", "note", "referrer_id", "wg_client_id"}
PERIODS = {"trial", "14d", "1m", "2m", "6m", "12m", "lifetime", "imported", "1", "6", "12"}

def register(app, authorized, db_connect):
    @app.patch("/api/clients/<int:client_id>")
    @authorized
    def update_client(client_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or (request.content_length or 0) > 8192:
            return jsonify(ok=False, error="Invalid JSON"), 400
        unknown = set(data) - FIELDS
        if unknown or not data:
            return jsonify(ok=False, error="Unknown or empty fields"), 400
        if "name" in data and (not isinstance(data["name"], str) or not data["name"].strip() or len(data["name"]) > 150):
            return jsonify(ok=False, error="Invalid name"), 400
        if "period" in data and data["period"] not in PERIODS:
            return jsonify(ok=False, error="Invalid period"), 400
        for field in ("start_date", "end_date"):
            if field in data and data[field] is not None:
                try: date.fromisoformat(data[field])
                except (ValueError, TypeError): return jsonify(ok=False, error="Invalid "+field), 400
        if "referrer_id" in data and data["referrer_id"] is not None:
            try: data["referrer_id"] = int(data["referrer_id"])
            except (TypeError, ValueError): return jsonify(ok=False, error="Invalid referrer"), 400
            if data["referrer_id"] == client_id:
                return jsonify(ok=False, error="Self-referral not allowed"), 400
        for field, maxlen in (("phone", 100), ("note", 2000), ("wg_client_id", 150)):
            if field in data and data[field] is not None and (not isinstance(data[field], str) or len(data[field]) > maxlen):
                return jsonify(ok=False, error="Invalid "+field), 400
        with db_connect() as con:
            row = con.execute("SELECT id,user_id FROM clients WHERE id=?", (client_id,)).fetchone()
            if not row: return jsonify(ok=False, error="Client not found"), 404
            if data.get("referrer_id") is not None and "referrer_id" in data:
                if not con.execute("SELECT 1 FROM clients WHERE id=?", (data["referrer_id"],)).fetchone():
                    return jsonify(ok=False, error="Referrer not found"), 400
            columns = list(data)
            try:
                con.execute("UPDATE clients SET "+",".join(k+"=?" for k in columns)+" WHERE id=?",
                            [data[k] for k in columns]+[client_id])
                con.execute("INSERT INTO client_history(user_id,client_id,event_type,description) VALUES (?,?,?,?)",
                            (row["user_id"],client_id,"updated","Updated: "+", ".join(columns)))
            except sqlite3.IntegrityError:
                return jsonify(ok=False, error="Database constraint violation"), 409
        return jsonify(ok=True, id=client_id)

    @app.delete("/api/clients/<int:client_id>")
    @authorized
    def delete_client(client_id):
        with db_connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row=con.execute("SELECT id,user_id,name FROM clients WHERE id=?", (client_id,)).fetchone()
            if not row: return jsonify(ok=False, error="Client not found"), 404
            referred=con.execute("SELECT COUNT(*) FROM clients WHERE referrer_id=?", (client_id,)).fetchone()[0]
            if referred:
                return jsonify(ok=False, error="Client has referrals; reassign before deleting"), 409
            con.execute("UPDATE client_history SET client_id=NULL WHERE client_id=?", (client_id,))
            con.execute("DELETE FROM clients WHERE id=?", (client_id,))
        return jsonify(ok=True, deleted=client_id, wg_status="unchanged")
