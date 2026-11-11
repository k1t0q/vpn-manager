"""WireGuard endpoints for SQLite staging. Reuses wg-easy; no Supabase dependency.
Requires WGEASY_PASSWORD in the staging service environment.
"""
import os
import re
import threading
import requests
from flask import jsonify, request, Response

WGEASY_URL = "http://127.0.0.1:51821"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_lock = threading.RLock()
_wg = requests.Session()

def _request(method, path, **kwargs):
    password = os.environ.get("WGEASY_PASSWORD")
    if not password:
        raise RuntimeError("WGEASY_PASSWORD is not configured for staging")
    with _lock:
        r = _wg.request(method, WGEASY_URL + path, timeout=15, **kwargs)
        if r.status_code in (401, 403):
            auth = _wg.post(WGEASY_URL + "/api/session",
                            json={"password": password}, timeout=15)
            auth.raise_for_status()
            if not auth.json().get("success"):
                raise RuntimeError("wg-easy authentication failed")
            r = _wg.request(method, WGEASY_URL + path, timeout=15, **kwargs)
        r.raise_for_status()
        return r

def _safe(c):
    return {key:c.get(key) for key in
            ("id","name","address","enabled","latestHandshakeAt","transferRx","transferTx")}

def register(app, authorized, db_connect):
    @app.get("/api/wg/clients")
    @authorized
    def wg_list():
        try:
            data=_request("GET","/api/wireguard/client").json()
            return jsonify(ok=True, clients=[_safe(c) for c in data], count=len(data))
        except (requests.RequestException,RuntimeError,ValueError) as e:
            app.logger.error("wg_list: %s",type(e).__name__)
            return jsonify(ok=False,error="WireGuard unavailable"),503

    @app.post("/api/wg/client")
    @authorized
    def wg_create():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or (request.content_length or 0)>2048:
            return jsonify(ok=False,error="Invalid request"),400
        name=data.get("name")
        if not isinstance(name,str) or not name.strip() or len(name)>150:
            return jsonify(ok=False,error="Invalid name"),400
        try:
            # wg-easy creation and lookup must not interleave in this worker.
            with _lock:
                before={c.get("id") for c in _request("GET","/api/wireguard/client").json()}
                _request("POST","/api/wireguard/client",json={"name":name.strip()})
                created=next((c for c in _request("GET","/api/wireguard/client").json()
                              if c.get("id") not in before),None)
            if not created:
                return jsonify(ok=False,error="Created VPN profile but cannot identify it; refresh list"),502
            return jsonify(ok=True,client=_safe(created))
        except (requests.RequestException,RuntimeError,ValueError) as e:
            app.logger.error("wg_create: %s",type(e).__name__)
            return jsonify(ok=False,error="WireGuard unavailable"),503

    def _id_or_error(cid):
        return bool(_ID.fullmatch(cid))

    @app.post("/api/wg/client/<cid>/enable")
    @authorized
    def wg_enable(cid):
        return _toggle(cid,"enable")

    @app.post("/api/wg/client/<cid>/disable")
    @authorized
    def wg_disable(cid):
        return _toggle(cid,"disable")

    def _toggle(cid,action):
        if not _id_or_error(cid):return jsonify(ok=False,error="Invalid ID"),400
        try:
            _request("POST",f"/api/wireguard/client/{cid}/{action}")
            return jsonify(ok=True)
        except (requests.RequestException,RuntimeError) as e:
            app.logger.error("wg_toggle: %s",type(e).__name__)
            return jsonify(ok=False,error="WireGuard unavailable"),503

    @app.get("/api/wg/client/<cid>/qr")
    @authorized
    def wg_qr(cid):
        return _download(cid,"qrcode.svg","image/svg+xml")

    @app.get("/api/wg/client/<cid>/config")
    @authorized
    def wg_config(cid):
        return _download(cid,"configuration","text/plain; charset=utf-8")

    def _download(cid,kind,mime):
        if not _id_or_error(cid):return jsonify(ok=False,error="Invalid ID"),400
        try:
            r=_request("GET",f"/api/wireguard/client/{cid}/{kind}")
            return Response(r.content,content_type=mime,headers={"Cache-Control":"no-store"})
        except (requests.RequestException,RuntimeError) as e:
            app.logger.error("wg_download: %s",type(e).__name__)
            return jsonify(ok=False,error="WireGuard unavailable"),503
