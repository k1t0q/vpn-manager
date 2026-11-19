"""CAESAR VPN MANAGER with protected production billing operations.

The service never changes Xray/Caddy directly. SQLite writes are allowlisted,
idempotent and backed up; the existing Xray reconciliation timer applies them.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import unquote
from wsgiref.simple_server import make_server, WSGIRequestHandler

try:
    from .hostkey_panel_ops import PanelOperations, OperationError
    from .xray_hostkey import active_subscription
except ImportError:
    from hostkey_panel_ops import PanelOperations, OperationError
    from xray_hostkey import active_subscription


PREFIX = "/manager"
LEGACY_PREFIX = "/manager-test"
ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get("CAESAR_DB", "/var/lib/caesar-manager/clients.db"))
CODE_FILE = Path(os.environ.get("CAESAR_PANEL_CODE_FILE", "/var/lib/caesar-manager/panel-code"))
URL_HOST = "vpn.caesarbot.ru"
MAX_BODY = 4096
TOKEN_RE = re.compile(r"^Bearer ([A-Za-z0-9_-]{30,100})$")
LINK_RE = re.compile(r"^/api/clients/([1-9][0-9]*)/link$")
HISTORY_RE = re.compile(r"^/api/clients/([1-9][0-9]*)/history$")
ACTION_RE = re.compile(r"^/api/clients/([1-9][0-9]*)/(edit|extend|toggle|access|provision|archive|restore)$")
FRONTEND_SCRIPTS = ('<script src="config.js?v=awg1"></script>'
                    '<script src="sqlite-manager.js?v=alignment1"></script>'
                    '<script src="push-manager.js?v=push1"></script>')
PILOT_SCRIPT = '<script src="/manager/panel.js"></script>'
PILOT_STYLE = '''<style>
/* Hide protected content until the session status is known. */
html:not(.caesar-panel-ready) #loginPane,
html:not(.caesar-panel-ready) #workspace{visibility:hidden!important}
#pilotBoot{position:fixed;inset:0;z-index:9999;display:grid;place-items:center;
background:#0e1015;color:#d9e3f5;font:500 15px system-ui,sans-serif}
#pilotBoot .pilot-boot-content{display:flex;align-items:center;gap:14px}
#pilotBoot .pilot-boot-spinner{width:22px;height:22px;border:3px solid #34465d;
border-top-color:#4c9df9;border-radius:50%;animation:pilot-boot-spin .9s linear infinite}
@keyframes pilot-boot-spin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){#pilotBoot .pilot-boot-spinner{animation:none}}
html.caesar-panel-ready #pilotBoot{display:none}
#enablePush,#testPush,#disablePush{display:none!important}
.pilot-note{background:#26364b;border:1px solid #55729b;border-radius:10px;padding:14px;
margin-bottom:14px;color:#e5edfa;line-height:1.45}
.pilot-devices{display:grid;gap:4px;text-align:left;font-size:12px}
.pilot-device-ok{color:#65d8a1}.pilot-device-wait{color:#f7c670}
.pilot-device-none{color:#aeb4c2}.pilot-copy{font-size:12px}
.vpn-access{display:inline-flex;align-items:center;gap:5px;border-radius:8px;padding:6px 9px;
font-size:12px;font-weight:750;white-space:nowrap;background:#243045;color:#dce8ff}
.vpn-access.on{background:#16372e;color:#69e0ac}
.vpn-access.partial{background:#45381e;color:#ffd17e}
.vpn-access.off,.vpn-access.expired{background:#40262a;color:#ff979d}
.vpn-access.pending{background:#3b3150;color:#ceacff}
.vpn-access.none{color:#aeb4c2}
.vpn-access-help{font-size:11px;color:#aeb4c2;margin-top:5px;line-height:1.35}
.subscription-cell{display:flex;align-items:flex-start;flex-direction:column;gap:7px;white-space:nowrap}
.subscription-cell strong{font-size:13px;font-weight:750}
.subscription-cell .status{display:inline-block}
.client-actions [data-action="access"].danger{background:#673237;color:#ffe3e5;border-color:#a35258}
/* Keep every client action visible even when the billing table overflows. */
.m-scroll.desktop{position:relative;max-width:100%;overflow-x:auto}
.m-scroll.desktop table{table-layout:fixed;min-width:1350px}
.m-scroll.desktop th,.m-scroll.desktop td{overflow-wrap:anywhere}
.m-scroll.desktop th:nth-child(1),.m-scroll.desktop td:nth-child(1){width:132px}
.m-scroll.desktop th:nth-child(2),.m-scroll.desktop td:nth-child(2){width:90px}
.m-scroll.desktop th:nth-child(3),.m-scroll.desktop td:nth-child(3){width:90px}
.m-scroll.desktop th:nth-child(4),.m-scroll.desktop td:nth-child(4){width:150px}
.m-scroll.desktop th:nth-child(5),.m-scroll.desktop td:nth-child(5){width:92px}
.m-scroll.desktop th:nth-child(6),.m-scroll.desktop td:nth-child(6){width:94px}
.m-scroll.desktop th:nth-child(7),.m-scroll.desktop td:nth-child(7){width:94px}
.m-scroll.desktop th:nth-child(8),.m-scroll.desktop td:nth-child(8){width:118px}
.m-scroll.desktop th:nth-child(9),.m-scroll.desktop td:nth-child(9){width:152px}
.m-scroll.desktop th:last-child,.m-scroll.desktop td:last-child{
position:sticky;right:0;z-index:2;width:270px;min-width:270px;max-width:270px;
background:#191d25;box-shadow:-9px 0 16px #0e1015b0,inset 1px 0 #303744}
.m-scroll.desktop th:last-child{z-index:3;background:#20252e}
.m-scroll.desktop tr:hover td:last-child{background:#202733}
.m-scroll.desktop .client-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px}
.m-scroll.desktop .client-actions button{width:100%;min-width:0;min-height:34px;
padding:6px 4px;font-size:11px;white-space:normal;overflow-wrap:anywhere;line-height:1.2}
</style>'''


def client_rows(db_path: Path, operations: PanelOperations | None = None) -> list[dict]:
    """Return billing and device *statuses*, never UUIDs or bearer tokens."""
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as con:
        con.row_factory = sqlite3.Row
        clients = con.execute("""
            SELECT id,name,phone,start_date,end_date,period,note,created_at,
                   referrer_id,wg_client_id FROM clients ORDER BY id DESC
        """).fetchall()
        devices = con.execute("""
            SELECT client_id,kind,manually_enabled,deployed
              FROM caesar_vless_devices
        """).fetchall()
        has_archives = con.execute("SELECT 1 FROM sqlite_master WHERE name='caesar_client_archives'").fetchone()
        archived = {row[0] for row in con.execute('SELECT client_id FROM caesar_client_archives')} if has_archives else set()
    allowed = operations.permitted_ids() if operations else set()
    device_map = defaultdict(dict)
    for d in devices:
        device_map[d["client_id"]][d["kind"]] = {"allowed": bool(d["manually_enabled"]),
                                                 "deployed": bool(d["deployed"])}
    result = []
    for c in clients:
        device_states = device_map[c['id']]
        is_archived = c['id'] in archived
        paid = active_subscription(c['period'], c['end_date'])
        deployed = sum(bool(d['deployed']) for d in device_states.values())
        target = sum(bool(d['allowed']) and paid and not is_archived for d in device_states.values())
        mismatch = any(bool(d['deployed']) != (bool(d['allowed']) and paid and not is_archived)
                       for d in device_states.values())
        if mismatch:
            vpn_state = 'pending'
        elif not device_states:
            vpn_state = 'none'
        elif not paid:
            vpn_state = 'expired'
        elif deployed == 2:
            vpn_state = 'on'
        elif deployed == 1:
            vpn_state = 'partial'
        else:
            vpn_state = 'off'
        result.append({**dict(c), 'devices': device_states,
                       'has_portal': len(device_states) == 2,
                       'subscription_active': paid,
                       'vpn_state': vpn_state,
                       'vpn_deployed_keys': deployed,
                       'vpn_target_keys': target,
                       'archived': is_archived,
                       'can_manage': (operations.allow_all if operations else False) or c['id'] in allowed})
    return result


def personal_link(db_path: Path, client_id: int) -> str | None:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as con:
        found = con.execute("SELECT token FROM caesar_connect_links WHERE client_id=?", (client_id,)).fetchone()
    return f"https://{URL_HOST}/connect/{found[0]}" if found else None


def personal_history(db_path: Path, client_id: int) -> list[dict]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT event_type,description,created_at FROM client_history
             WHERE client_id=? ORDER BY created_at DESC,id DESC LIMIT 100
        """, (client_id,)).fetchall()
    return [dict(row) for row in rows]


def panel_html(source: Path = ROOT / "index.html") -> bytes:
    page = source.read_text(encoding="utf-8")
    if page.count(FRONTEND_SCRIPTS) != 1:
        raise ValueError("Unexpected old panel scripts; refusing to load legacy AWG JavaScript")
    return (page.replace(FRONTEND_SCRIPTS, PILOT_SCRIPT)
                .replace('<body>', '<body><div id="pilotBoot" role="status" aria-live="polite">'
                         '<div class="pilot-boot-content"><span class="pilot-boot-spinner" '
                         'aria-hidden="true"></span><span>Загружаем VPN MANAGER…</span></div></div>', 1)
                .replace("AmneziaWG", "VLESS")
                .replace("CAESAR BOT", "CAESAR VPN")
                .replace("<h1>VPN MANAGER</h1>", "<h1>VPN MANAGER · HOSTKEY</h1>")
                .replace("Версия v1.80 · 13.11.2026", "CAESAR VPN · VLESS · HOSTKEY")
                .replace('<link rel="stylesheet" href="styles.css?v=5.16">',
                         '<link rel="stylesheet" href="/manager/styles.css">')
                .replace("</head>", PILOT_STYLE + "</head>", 1)
                .replace('Введите код администратора для управления VPN.',
                         'Введите код администратора CAESAR VPN для входа в HOSTKEY.')
                .replace('<div class="m-grid layout">',
                         '<p class="pilot-note">🔒 CAESAR VPN MANAGER: защищённое управление клиентами. '
                         'Изменения VPN применяются автоматически в течение пяти минут, '
                         'перед каждым изменением сохраняется резервная копия.</p>'
                         '<div class="m-grid layout">', 1)
                .replace('id="pushStatus" class="m-muted">За 7, 3 и 1 день до окончания подписки.',
                         'id="pushStatus" class="m-muted">Уведомления включим после запуска панели.')
                .replace('<th>VLESS</th>', '<th>Телефон / ПК</th>')
                .replace('<th>Осталось дней</th><th>Статус</th><th>Действия</th>',
                         '<th>Подписка</th><th>Доступ VPN</th><th>Действия</th>')
                ).encode("utf-8")


class Panel:
    def __init__(self, db_path: Path = DB, code_file: Path = CODE_FILE,
                 frontend: Path = ROOT / "index.html", javascript: Path = ROOT / "server/hostkey_panel.js"):
        self.db_path = db_path
        self.code_file = code_file
        self.frontend = frontend
        self.javascript = javascript
        self.ops = PanelOperations.from_environment(db_path)
        self.sessions: dict[str, float] = {}
        self.attempts: dict[str, list[float]] = defaultdict(list)

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = unquote(environ.get("PATH_INFO", ""))
        legacy = path == LEGACY_PREFIX or path.startswith(LEGACY_PREFIX + '/')
        if legacy and method == 'GET' and path in (LEGACY_PREFIX, LEGACY_PREFIX + '/'):
            start_response('308 Permanent Redirect', [('Location', PREFIX + '/'),
                                                      ('Cache-Control', 'no-store'),
                                                      ('Content-Length', '0')])
            return [b'']
        if legacy:
            subpath = path[len(LEGACY_PREFIX):]
        elif path == PREFIX or path.startswith(PREFIX + '/'):
            subpath = path[len(PREFIX):]
        else:
            return self._respond(start_response, "404 Not Found", {"ok": False, "error": "Not found"})
        if method == "GET" and subpath in ("", "/"):
            try:
                page = panel_html(self.frontend)
            except (OSError, ValueError):
                return self._respond(start_response, "503 Service Unavailable", {"ok": False, "error": "Panel unavailable"})
            return self._respond(start_response, "200 OK", page, content_type="text/html; charset=utf-8")
        if method == "GET" and subpath == "/panel.js":
            try:
                data = self.javascript.read_bytes()
            except OSError:
                return self._respond(start_response, "503 Service Unavailable", {"ok": False, "error": "Panel unavailable"})
            return self._respond(start_response, "200 OK", data, content_type="application/javascript; charset=utf-8")
        if method == "GET" and subpath == "/styles.css":
            try:
                data = (ROOT / "styles.css").read_bytes()
            except OSError:
                return self._respond(start_response, "503 Service Unavailable", {"ok": False, "error": "Styles unavailable"})
            return self._respond(start_response, "200 OK", data, content_type="text/css; charset=utf-8")
        if method == "GET" and subpath == "/health":
            return self._respond(start_response, "200 OK", {"ok": True, "version": "caesar-hostkey-panel-2",
                                                                "xray_mode": True, "read_only": not self.ops.enabled,
                                                                "full_access": self.ops.allow_all,
                                                                "fast_sync": bool(self.ops.sync_signal_path and self.ops.sync_signal_path.is_file())})
        if method not in ("GET", "POST"):
            return self._respond(start_response, "405 Method Not Allowed", {"ok": False, "error": "Method not allowed"})
        if method == "POST":
            origin = environ.get("HTTP_ORIGIN", "")
            if origin != f"https://{URL_HOST}":
                return self._respond(start_response, "403 Forbidden", {"ok": False, "error": "Origin forbidden"})
        if method == "POST" and subpath == "/auth/login":
            return self._login(environ, start_response)
        if not self._authenticated(environ):
            return self._respond(start_response, "401 Unauthorized", {"ok": False, "error": "Unauthorized"})
        try:
            if method == "POST" and subpath == "/auth/logout":
                found = TOKEN_RE.fullmatch(environ.get("HTTP_AUTHORIZATION", ""))
                self.sessions.pop(sha256(found.group(1).encode()).hexdigest(), None)
                return self._respond(start_response, "200 OK", {"ok": True})
            if method == "GET" and subpath == "/api/clients":
                return self._respond(start_response, "200 OK", {"ok": True, "clients": client_rows(self.db_path, self.ops),
                                                                    "pilot_enabled": self.ops.enabled,
                                                                    "full_access": self.ops.allow_all,
                                                                    "fast_sync": bool(self.ops.sync_signal_path and self.ops.sync_signal_path.is_file())})
            if method == "POST":
                action = ACTION_RE.fullmatch(subpath)
                if action or subpath == "/api/clients":
                    payload = self._json_body(environ)
                    identifier = environ.get('HTTP_IDEMPOTENCY_KEY', '')
                    result = self.ops.execute(action.group(2) if action else 'create',
                                              int(action.group(1)) if action else None, payload, identifier)
                    return self._respond(start_response, "201 Created" if not action else "200 OK", result)
            link = LINK_RE.fullmatch(subpath)
            if method == "GET" and link:
                url = personal_link(self.db_path, int(link.group(1)))
                if url is None:
                    return self._respond(start_response, "404 Not Found", {"ok": False, "error": "Personal link is not yet provisioned"})
                return self._respond(start_response, "200 OK", {"ok": True, "url": url})
            history = HISTORY_RE.fullmatch(subpath)
            if method == "GET" and history:
                return self._respond(start_response, "200 OK", {"ok": True, "history": personal_history(self.db_path, int(history.group(1)))})
        except OperationError as error:
            status = {400: '400 Bad Request', 403: '403 Forbidden', 404: '404 Not Found',
                      409: '409 Conflict'}.get(error.status, '400 Bad Request')
            return self._respond(start_response, status, {"ok": False, "error": str(error)})
        except (OSError, sqlite3.Error):
            return self._respond(start_response, "503 Service Unavailable", {"ok": False, "error": "Database unavailable"})
        return self._respond(start_response, "405 Method Not Allowed", {"ok": False, "error": "Method not allowed"})

    @staticmethod
    def _json_body(environ) -> dict:
        try:
            length = int(environ.get('CONTENT_LENGTH', '0'))
            if length < 2 or length > MAX_BODY:
                raise ValueError()
            payload = json.loads(environ['wsgi.input'].read(length))
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise OperationError('Invalid JSON body') from None

    def _authenticated(self, environ) -> bool:
        found = TOKEN_RE.fullmatch(environ.get("HTTP_AUTHORIZATION", ""))
        if not found:
            return False
        digest = sha256(found.group(1).encode()).hexdigest()
        return self.sessions.get(digest, 0) > time.time()

    def _login(self, environ, start_response):
        # The service binds loopback and is accessed only via Caddy. Split XFF
        # to avoid letting one remote user lock out every administrator.
        address = environ.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip() or environ.get("REMOTE_ADDR", "unknown")
        now = time.time()
        attempts = [when for when in self.attempts[address] if when > now - 300]
        self.attempts[address] = attempts
        if len(attempts) >= 10:
            return self._respond(start_response, "429 Too Many Requests", {"ok": False, "error": "Wait five minutes"})
        attempts.append(now)
        length = environ.get("CONTENT_LENGTH", "0")
        try:
            count = int(length)
            if count <= 0 or count > MAX_BODY:
                raise ValueError()
            payload = json.loads(environ["wsgi.input"].read(count))
            code = payload.get("code", "") if isinstance(payload, dict) else ""
            if not isinstance(code, str):
                raise ValueError()
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self._respond(start_response, "400 Bad Request", {"ok": False, "error": "Invalid request"})
        try:
            configured = self.code_file.read_text(encoding="utf-8").strip()
        except OSError:
            return self._respond(start_response, "503 Service Unavailable", {"ok": False, "error": "Login not configured"})
        if len(configured) < 24 or not hmac.compare_digest(configured, code):
            return self._respond(start_response, "401 Unauthorized", {"ok": False, "error": "Unauthorized"})
        self.attempts.pop(address, None)
        token = secrets.token_urlsafe(32)
        self.sessions[sha256(token.encode()).hexdigest()] = now + 43200
        return self._respond(start_response, "200 OK", {"ok": True, "access_token": token, "expires_in": 43200})

    @staticmethod
    def _respond(start_response, status: str, data: dict | bytes, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if isinstance(data, dict) else data
        headers = [("Content-Type", content_type), ("Content-Length", str(len(body))),
                   ("Cache-Control", "no-store, private"), ("Referrer-Policy", "no-referrer"),
                   ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                   ("Content-Security-Policy", "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")]
        start_response(status, headers)
        return [body]


app = Panel()


if __name__ == "__main__":
    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *_args):
            pass  # Never log bearer URLs or authorization headers.

    with make_server("127.0.0.1", 5059, app, handler_class=QuietHandler) as server:
        server.serve_forever()
