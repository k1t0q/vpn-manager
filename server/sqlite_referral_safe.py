"""Manual, idempotent referral awards for SQLite staging. Does not touch WireGuard."""
import sqlite3
from datetime import date,timedelta
from flask import jsonify,request

BONUS={"1m":14,"1":14,"6m":30,"6":30,"12m":60,"12":60}

def register(app,authorized,db_connect,managed=None):
    @app.post("/api/referrals/award-safe")
    @authorized
    def award_referral_safe():
        d=request.get_json(silent=True)
        if not isinstance(d,dict):return jsonify(ok=False,error="Expected JSON"),400
        try:
            referred_id=int(d["client_id"])
        except (KeyError,ValueError,TypeError):
            return jsonify(ok=False,error="Invalid client_id"),400
        if app.config.get('MANAGED_AWG'):
            # Resolve the recipient through the managed DB, never the old clone.
            return jsonify(ok=False,error='Use the managed referral route'),409
        try:
            with db_connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("""CREATE TABLE IF NOT EXISTS referral_awards (
                    referred_client_id INTEGER PRIMARY KEY,
                    referrer_client_id INTEGER NOT NULL,
                    bonus_days INTEGER NOT NULL,
                    awarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                referred=con.execute("SELECT id,name,period,referrer_id FROM clients WHERE id=?",(referred_id,)).fetchone()
                if referred is None:return jsonify(ok=False,error="Referred client not found"),404
                referrer_id=referred["referrer_id"]
                if referrer_id is None or int(referrer_id)==referred_id:
                    return jsonify(ok=False,error="No valid referrer"),400
                selected_period=d.get("bonus_period",referred["period"])
                if str(selected_period) not in ("1m","6m","12m","1","6","12"):
                    return jsonify(ok=False,error="Invalid bonus period"),400
                days=BONUS.get(str(selected_period))
                if not days:return jsonify(ok=False,error="Tariff has no referral bonus"),400
                referrer=con.execute("SELECT id,name,user_id,period,end_date FROM clients WHERE id=?",(referrer_id,)).fetchone()
                if referrer is None:return jsonify(ok=False,error="Referrer not found"),404
                if referrer["period"]=="lifetime":
                    return jsonify(ok=False,error="Referrer is lifetime; no extension needed"),409
                existing=con.execute("SELECT bonus_days FROM referral_awards WHERE referred_client_id=?",(referred_id,)).fetchone()
                if existing:return jsonify(ok=False,error="Referral bonus already awarded via this endpoint"),409
                today=date.today()
                try:base=max(today,date.fromisoformat(str(referrer["end_date"])[:10]))
                except (ValueError,TypeError):base=today
                new_end=(base+timedelta(days=days)).isoformat()
                con.execute("INSERT INTO referral_awards(referred_client_id,referrer_client_id,bonus_days) VALUES(?,?,?)",(referred_id,referrer_id,days))
                con.execute("UPDATE clients SET end_date=? WHERE id=?",(new_end,referrer_id))
                con.execute("INSERT INTO client_history (user_id,client_id,event_type,description) VALUES (?,?,?,?)",
                    (referrer["user_id"],referrer_id,"referral_bonus","Referral bonus +%s days (selected tariff %s) from client #%s"%(days,selected_period,referred_id)))
            return jsonify(ok=True,client_id=referred_id,referrer_id=referrer_id,bonus_days=days,new_end_date=new_end,wireguard_unchanged=True)
        except sqlite3.IntegrityError:
            return jsonify(ok=False,error="Referral bonus already awarded"),409

