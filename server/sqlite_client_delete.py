"""Delete an SQLite client and their linked wg-easy peer through authenticated staging API."""
import re
import sqlite3
import requests
from flask import jsonify
from sqlite_wireguard_staging import _request, _lock

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

def register(app, authorized, db_connect):
    @app.delete("/api/clients/<int:client_id>")
    @authorized
    def delete_sqlite_client(client_id):
        try:
            # Keep database write lock while validating and deleting the exact linked peer.
            with db_connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row=con.execute("SELECT id,name,wg_client_id FROM clients WHERE id=?",(client_id,)).fetchone()
                if row is None:
                    return jsonify(ok=False,error="Клиент не найден"),404
                peer_id=row["wg_client_id"]
                if peer_id is not None and str(peer_id).strip():
                    peer_id=str(peer_id)
                    if not _ID.fullmatch(peer_id):
                        return jsonify(ok=False,error="Некорректный ID WireGuard: удаление отменено"),409
                    # Never delete a peer that another client record also references.
                    shared=con.execute("SELECT COUNT(*) FROM clients WHERE wg_client_id=? AND id<>?",(peer_id,client_id)).fetchone()[0]
                    if shared:
                        return jsonify(ok=False,error="WireGuard-профиль привязан к нескольким клиентам. Удаление отменено"),409
                columns={c["name"] for c in con.execute("PRAGMA table_info(clients)")}
                if "referrer_id" in columns:
                    linked=con.execute("SELECT COUNT(*) FROM clients WHERE referrer_id=?",(client_id,)).fetchone()[0]
                    if linked:
                        return jsonify(ok=False,error="Клиент указан как пригласивший. Удаление отменено"),409
                # Check dependent references before any irreversible WireGuard deletion.
                # wg-easy delete is performed only after confirming the peer exists.
                if peer_id:
                    try:
                        with _lock:
                            peers=_request("GET","/api/wireguard/client").json()
                            if not isinstance(peers,list):
                                return jsonify(ok=False,error="Некорректный ответ WireGuard"),503
                            if not any(str(p.get("id"))==peer_id for p in peers):
                                return jsonify(ok=False,error="WireGuard-профиль не найден. Удаление отменено"),409
                            _request("DELETE",f"/api/wireguard/client/{peer_id}")
                    except (requests.RequestException,RuntimeError,ValueError) as e:
                        app.logger.error("client_delete_wg: %s",type(e).__name__)
                        return jsonify(ok=False,error="Не удалось удалить WireGuard-профиль. Клиент сохранён"),503
                con.execute("DELETE FROM client_history WHERE client_id=?",(client_id,))
                con.execute("DELETE FROM clients WHERE id=?",(client_id,))
            return jsonify(ok=True,id=client_id,wireguard_deleted=bool(peer_id))
        except sqlite3.IntegrityError:
            app.logger.error("client_delete: SQLite integrity error after peer deletion")
            return jsonify(ok=False,error="Ошибка связей SQLite. Проверьте WireGuard перед повтором"),409
        except sqlite3.Error:
            app.logger.exception("client_delete: database error")
            return jsonify(ok=False,error="Ошибка базы данных. Проверьте WireGuard перед повтором"),500
