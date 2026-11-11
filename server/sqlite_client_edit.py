"""SQLite client edit endpoint. Register on staging with register(app, authorized, db_connect)."""
import sqlite3
from datetime import date
from flask import jsonify,request

def register(app,authorized,db_connect):
    @app.patch("/api/clients/<int:client_id>")
    @authorized
    def edit_sqlite_client(client_id):
        d=request.get_json(silent=True)
        if not isinstance(d,dict) or (request.content_length or 0)>8192:
            return jsonify(ok=False,error="Invalid JSON"),400
        allowed={"name","phone","note","start_date","end_date","period"}
        if any(k not in allowed|{"id"} for k in d):
            return jsonify(ok=False,error="Unknown field"),400
        if not d.get("name") or len(str(d["name"]))>150:
            return jsonify(ok=False,error="Name required (max 150)"),400
        if d.get("period") not in ("1m","6m","12m","lifetime","1","6","12","trial","14d","2m","imported"):
            return jsonify(ok=False,error="Invalid period"),400
        try:
            start=date.fromisoformat(d["start_date"])
            end=None if d["period"]=="lifetime" else date.fromisoformat(d["end_date"]).isoformat()
        except (ValueError,TypeError,KeyError):
            return jsonify(ok=False,error="Invalid dates"),400
        with db_connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row=con.execute("SELECT id,user_id,period,end_date FROM clients WHERE id=?",(client_id,)).fetchone()
            if row is None:return jsonify(ok=False,error="Client not found"),404
            con.execute("UPDATE clients SET name=?,phone=?,note=?,start_date=?,end_date=?,period=? WHERE id=?",(
                str(d["name"]).strip(),str(d.get("phone") or "")[:100],str(d.get("note") or "")[:2000],
                start.isoformat(),end,d["period"],client_id))
            con.execute("INSERT INTO client_history (user_id,client_id,event_type,description) VALUES (?,?,?,?)",(
                row["user_id"],client_id,"edited","Client details updated"))
        return jsonify(ok=True,id=client_id)
