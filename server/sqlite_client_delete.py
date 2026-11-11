"""Authenticated deletion of a SQLite client record; never changes WireGuard peers."""
import sqlite3
from flask import jsonify

def register(app, authorized, db_connect):
    @app.delete("/api/clients/<int:client_id>")
    @authorized
    def delete_sqlite_client(client_id):
        try:
            with db_connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("PRAGMA foreign_keys=ON")
                row=con.execute("SELECT id,name FROM clients WHERE id=?",(client_id,)).fetchone()
                if row is None:
                    return jsonify(ok=False,error="Client not found"),404
                # Refuse deletion when this client is a referrer; do not silently change other clients.
                cols={c["name"] for c in con.execute("PRAGMA table_info(clients)").fetchall()}
                if "referrer_id" in cols:
                    n=con.execute("SELECT COUNT(*) FROM clients WHERE referrer_id=?",(client_id,)).fetchone()[0]
                    if n:
                        return jsonify(ok=False,error="Нельзя удалить: клиент указан как пригласивший у других клиентов"),409
                # Preserve the history of other clients. Delete only this client's own events.
                con.execute("DELETE FROM client_history WHERE client_id=?",(client_id,))
                con.execute("DELETE FROM clients WHERE id=?",(client_id,))
            return jsonify(ok=True,id=client_id)
        except sqlite3.IntegrityError:
            return jsonify(ok=False,error="Удаление заблокировано связанными записями. Данные не изменены."),409
        except sqlite3.Error:
            return jsonify(ok=False,error="Ошибка базы данных при удалении"),500
