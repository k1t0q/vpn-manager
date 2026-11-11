#!/usr/bin/env python3
"""Read-only live checks + isolated SQLite copy CRUD smoke tests."""
import hashlib, importlib, json, os, secrets, shutil, sqlite3, sys, tempfile, time
from pathlib import Path
from flask import Flask
BASE=Path("/opt/vpn-manager")
sys.path.insert(0,str(BASE))
DB=BASE/"data/clients-staging.db"
def report(name,ok,detail=""):
    print(("PASS" if ok else "FAIL")+" | "+name+(" | "+str(detail)[:180] if detail else ""),flush=True)
    return ok
passed=[]
try:
    con=sqlite3.connect(str(DB))
    n=con.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    integrity=con.execute("PRAGMA quick_check").fetchone()[0]
    con.close()
    passed.append(report("SQLite integrity",integrity=="ok",integrity))
    passed.append(report("SQLite clients",n>=59,n))
except Exception as e:
    report("SQLite",False,type(e).__name__);sys.exit(2)
try:
    import staging_api as api
    paths={r.rule for r in api.app.url_map.iter_rules()}
    for path in ("/health","/auth/login","/api/clients","/api/wg/clients","/api/clients/create","/api/referrals/award"):
        passed.append(report("Route "+path,path in paths))
    token=secrets.token_urlsafe(32)
    digest=hashlib.sha256(token.encode()).hexdigest()
    with api.db_connect() as con:
        con.execute("INSERT INTO auth_sessions(token_hash,expires_at) VALUES (?,?)",(digest,int(time.time())+120))
    try:
        client=api.app.test_client()
        hdr={"Authorization":"Bearer "+token}
        for path in ("/health","/api/clients","/api/wg/clients"):
            start=time.monotonic()
            response=client.get(path,headers=hdr)
            payload=response.get_json(silent=True) or {}
            detail="HTTP "+str(response.status_code)
            if path=="/api/clients":detail+="; count="+str(len(payload.get("clients",[])))
            if path=="/api/wg/clients":detail+="; count="+str(payload.get("count","unknown"))
            detail+="; "+str(round(time.monotonic()-start,2))+"s"
            passed.append(report("GET "+path,response.status_code==200,detail))
    finally:
        with api.db_connect() as con:
            con.execute("DELETE FROM auth_sessions WHERE token_hash=?",(digest,))
except Exception as e:
    report("API tests",False,type(e).__name__+": "+str(e)[:100]);passed.append(False)
try:
    from vpn_clients_extension import register as crud
    with tempfile.TemporaryDirectory(prefix="vpn-sqlite-smoke-") as td:
        temp=Path(td)/"test.db"
        src=sqlite3.connect(str(DB));dest=sqlite3.connect(str(temp));src.backup(dest);src.close();dest.close()
        def connect():
            c=sqlite3.connect(str(temp),timeout=5);c.row_factory=sqlite3.Row;return c
        app=Flask("sqlite-smoke")
        crud(app,lambda f:f,connect)
        c=app.test_client()
        uid="smoke-"+secrets.token_hex(6)
        response=c.post("/api/clients/create",json={"name":"Smoke test","user_id":uid,"period":"1"})
        obj=response.get_json(silent=True) or {}
        passed.append(report("Create on isolated DB",response.status_code==201,"HTTP "+str(response.status_code)))
        if response.status_code==201:
            cid=obj["id"]
            response=c.post("/api/clients/"+str(cid)+"/extend",json={"period":"1"})
            passed.append(report("Extend on isolated DB",response.status_code==200,"HTTP "+str(response.status_code)))
            response=c.get("/api/clients/"+str(cid)+"/history")
            passed.append(report("History on isolated DB",response.status_code==200,"HTTP "+str(response.status_code)))
except Exception as e:
    report("Isolated CRUD",False,type(e).__name__+": "+str(e)[:100]);passed.append(False)
print("SUMMARY "+str(sum(passed))+"/"+str(len(passed))+" passed")
sys.exit(0 if all(passed) else 1)
