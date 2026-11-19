"""Read-only personalized VLESS subscriptions for UXWay and INCY.

Expose this WSGI app only through Caddy to the /sub/* route. Subscription
tokens are bearer credentials and must not be written to logs or analytics.
"""

from __future__ import annotations

from datetime import date, datetime, time
from html import escape
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlencode, quote

from xray_hostkey import MOSCOW, active_subscription


DOMAIN = os.environ.get("CAESAR_VLESS_HOST", "vpn.caesarbot.ru")
DB_PATH = Path(os.environ.get("CAESAR_DB", "/var/lib/caesar-manager/clients.db"))
WS_PATH = os.environ.get("CAESAR_VLESS_WS_PATH", "/vless-ws")
TOKEN_PATTERN = re.compile(r"^/sub/([A-Za-z0-9_-]{32,64})$")
CONNECT_PATTERN = re.compile(r"^/(?:connect/)?([A-Za-z0-9_-]{32,64})$")


def expiry_timestamp(period: str | None, end: str | None) -> int | None:
    if period == "lifetime" or not end:
        return None
    try:
        return int(datetime.combine(date.fromisoformat(end[:10]), time(23, 59, 59), MOSCOW).timestamp())
    except ValueError:
        return None


def vless_uri(identifier: str, domain: str = DOMAIN, path: str = WS_PATH) -> str:
    query = urlencode({
        "encryption": "none", "security": "tls", "sni": domain,
        "type": "ws", "host": domain, "path": path,
    })
    return f"vless://{identifier}@{domain}:443?{query}#{quote('CAESAR VPN')}"


def fetch_subscription(db_path: Path, token: str):
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as con:
        con.row_factory = sqlite3.Row
        return con.execute("""
            SELECT d.uuid, d.kind, d.manually_enabled, d.deployed,
                   c.period, c.end_date
              FROM caesar_vless_devices d
              JOIN clients c ON c.id=d.client_id
             WHERE d.token=?
        """, (token,)).fetchone()


def fetch_landing(db_path: Path, token: str):
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as con:
        con.row_factory = sqlite3.Row
        return con.execute("""
            SELECT c.period, c.end_date,
                   phone.token AS phone_token, phone.deployed AS phone_deployed,
                   phone.manually_enabled AS phone_enabled,
                   pc.token AS pc_token, pc.deployed AS pc_deployed,
                   pc.manually_enabled AS pc_enabled
              FROM caesar_connect_links link
              JOIN clients c ON c.id=link.client_id
              JOIN caesar_vless_devices phone ON phone.client_id=c.id AND phone.kind='phone'
              JOIN caesar_vless_devices pc ON pc.client_id=c.id AND pc.kind='pc'
             WHERE link.token=?
        """, (token,)).fetchone()


def landing_html(row) -> str:
    active = active_subscription(row["period"], row["end_date"])
    ready = active and row["phone_deployed"] and row["pc_deployed"] and row["phone_enabled"] and row["pc_enabled"]
    if row["period"] == "lifetime":
        period = "Подписка: Навсегда"
    elif row["end_date"]:
        try:
            period = "Подписка до " + date.fromisoformat(row["end_date"][:10]).strftime("%d.%m.%Y")
        except ValueError:
            period = "Срок подписки уточняется"
    else:
        period = "Срок подписки уточняется"
    mobile_sub = f"https://{DOMAIN}/sub/{row['phone_token']}"
    pc_sub = f"https://{DOMAIN}/sub/{row['pc_token']}"
    # UXWay already passed owner-side deep-link testing; INCY /import/ is
    # documented by its developers, pending a real Windows owner test.
    links = (f'<a class="primary" href="{escape("uxway://add/" + mobile_sub, quote=True)}">'
             '📱 Подключить телефон · UXWay</a>'
             f'<a class="secondary" href="{escape("incy://import/" + pc_sub, quote=True)}">'
             '💻 Подключить компьютер · INCY</a>') if ready else (
             '<p class="notice">Подключение ещё не активировано или подписка закончилась.</p>')
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>CAESAR VPN — Подключение</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#10131a;color:#f7f8fd;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;padding:24px}}
main{{width:min(100%,480px);padding:34px 28px;border:1px solid #30394b;border-radius:22px;background:linear-gradient(150deg,#1c2433,#141821);box-shadow:0 18px 55px #0006}}
h1{{margin:0;font-size:32px;letter-spacing:1px}}.tag{{margin-top:10px;color:#9dadc6;font-size:14px}}.plan{{margin:26px 0 24px;padding:14px 16px;border:1px solid #3b4a60;border-radius:12px;background:#202a3a;color:#d7e3f4}}
a{{display:block;text-align:center;margin:11px 0;padding:15px 12px;border-radius:12px;text-decoration:none;font-weight:700;transition:filter .15s}}a:hover{{filter:brightness(1.15)}}.primary{{background:#5c88ff;color:#fff}}.secondary{{background:#2b3d59;color:#f7f8fd;border:1px solid #5471a4}}
.foot{{margin:22px 0 0;font-size:13px;line-height:1.6;color:#91a1bd}}.notice{{padding:14px;border-radius:12px;background:#493634;color:#ffe1db}}
@media (max-width:600px){{body{{display:block;min-height:100svh;padding:28px 18px 36px}}main{{margin:0 auto}}}}
</style></head><body><main><h1>CAESAR VPN</h1><div class="tag">Ваше персональное подключение</div>
<div class="plan">{escape(period)}</div>{links}
<p class="foot">Для телефона используйте UXWay, для компьютера — INCY. У каждого устройства свой ключ. Никому не пересылайте эту страницу.</p>
</main></body></html>"""


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "")
    headers = [("Content-Type", "text/plain; charset=utf-8"),
               ("Cache-Control", "no-store, private"),
               ("X-Content-Type-Options", "nosniff"),
               ("X-Robots-Tag", "noindex, nofollow"),
               ("Access-Control-Allow-Origin", "*")]

    def respond(status: str, body: str, extra=(), content_type="text/plain; charset=utf-8"):
        data = body.encode("utf-8")
        start_response(status, [("Content-Type", content_type)] + headers[1:] + list(extra) + [("Content-Length", str(len(data)))])
        return [data] if method != "HEAD" else [b""]

    if method == "OPTIONS":
        return respond("204 No Content", "", [("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")])
    if method not in ("GET", "HEAD"):
        return respond("405 Method Not Allowed", "Method not allowed\n")
    landing = CONNECT_PATTERN.fullmatch(path)
    if landing:
        try:
            row = fetch_landing(DB_PATH, landing.group(1))
        except (sqlite3.Error, OSError):
            return respond("503 Service Unavailable", "Portal temporarily unavailable\n")
        if row is None:
            return respond("404 Not Found", "Not found\n")
        return respond("200 OK", landing_html(row), content_type="text/html; charset=utf-8")
    match = TOKEN_PATTERN.fullmatch(path)
    if not match:
        return respond("404 Not Found", "Not found\n")
    try:
        row = fetch_subscription(DB_PATH, match.group(1))
    except (sqlite3.Error, OSError):
        return respond("503 Service Unavailable", "Subscription temporarily unavailable\n")
    if row is None:
        return respond("404 Not Found", "Not found\n")
    if not row["manually_enabled"] or not active_subscription(row["period"], row["end_date"]):
        return respond("410 Gone", "Subscription inactive\n")
    if not row["deployed"]:
        return respond("503 Service Unavailable", "Subscription not yet activated\n")
    expires = expiry_timestamp(row["period"], row["end_date"])
    info = "upload=0; download=0; total=0"
    if expires is not None:
        info += f"; expire={expires}"
    return respond("200 OK", vless_uri(row["uuid"]) + "\n", [("Subscription-Userinfo", info),
                                                         ("Profile-Title", "CAESAR VPN")])


# Gunicorn entry point, or any WSGI-compatible server on 127.0.0.1.
app = application


if __name__ == "__main__":
    # Built-in server is enough for the low-traffic pilot. Production may use
    # Gunicorn's WSGI workers without changing the application.
    from wsgiref.simple_server import make_server, WSGIRequestHandler

    class NoCredentialAccessLog(WSGIRequestHandler):
        def log_message(self, *_args):
            # URLs are bearer credentials; never write them to the journal.
            pass

    with make_server("127.0.0.1", 5058, application, handler_class=NoCredentialAccessLog) as server:
        server.serve_forever()
