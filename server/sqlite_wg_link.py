"""Atomic SQLite linkage for an already-created WireGuard peer.
Install on staging only: from sqlite_wg_link import register as register_link
register_link(app, authorized, db_connect)
"""
import re
import sqlite3
import requests
from flask import jsonify, request
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

def register(app, authorized, db_connect):
    @app.post("/api/clients/<int:client_id>/wireguard")
    @authorized
    def link_wireguard(client_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(ok=False, error="Expected JSON object"), 400
        wg_id = data.get("wg_client_id")
        if not isinstance(wg_id, str) or not _ID.fullmatch(wg_id):
            return jsonify(ok=False, error="Invalid WireGuard ID"), 400
        try:
            if not app.config.get("AWG_TOKEN"):
                return jsonify(ok=False,error="AWG bridge not configured"),503
            try:
                response=requests.get(app.config["AWG_URL"].rstrip("/")+"/api/wg/clients",headers={"X-VPN-Token":app.config["AWG_TOKEN"]},timeout=15)
                response.raise_for_status()
                peers=response.json()["clients"]
                if not any(str(p.get("id"))==wg_id for p in peers):
                    return jsonify(ok=False,error="AWG profile not found; no linkage changed"),409
            except (requests.RequestException,ValueError,KeyError,TypeError):
                return jsonify(ok=False,error="Unable to verify AWG profile"),503
            with db_connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT id,wg_client_id,user_id FROM clients WHERE id=?", (client_id,)).fetchone()
                if row is None:
                    return jsonify(ok=False,error="Client not found"),404
                if row["wg_client_id"] and row["wg_client_id"] != wg_id:
                    return jsonify(ok=False,error="Client already linked"),409
                other = con.execute("SELECT id FROM clients WHERE wg_client_id=? AND id<>?", (wg_id,client_id)).fetchone()
                if other:
                    return jsonify(ok=False,error="WireGuard peer already linked to another client"),409
                con.execute("UPDATE clients SET wg_client_id=? WHERE id=?", (wg_id,client_id))
                con.execute("INSERT INTO client_history (user_id,client_id,event_type,description) VALUES (?,?,?,?)", (row["user_id"],client_id,"wg_linked","WireGuard peer linked"))
            return jsonify(ok=True,id=client_id,wg_client_id=wg_id)
        except sqlite3.IntegrityError:
            return jsonify(ok=False,error="Database constraint violation"),409
