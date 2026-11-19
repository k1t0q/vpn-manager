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
UXWAY_IOS = "https://apps.apple.com/ru/app/uxway/id6762559624"
UXWAY_ANDROID = "https://play.google.com/store/apps/details?id=com.buttone.client"
INCY_WINDOWS = "https://github.com/INCY-DEV/incy-platforms/releases/download/latest/incy-windows-setup.exe"


def subscription_display(period: str | None, start: str | None, end: str | None,
                         today: date | None = None) -> dict:
    """Render the paid-through date using the same Moscow date rule as Xray.

    The final paid-through day counts as one day remaining. The progress
    denominator matches the legacy client portal: first start to current end.
    """
    if period == "lifetime":
        return {"lifetime": True, "end": None, "days": None, "progress": None}
    today = today or datetime.now(MOSCOW).date()
    try:
        paid_until = date.fromisoformat(end[:10]) if end else None
    except ValueError:
        paid_until = None
    if paid_until is None:
        return {"lifetime": False, "end": None, "days": None, "progress": None}
    days = max(0, (paid_until - today).days + 1)
    try:
        start_on = date.fromisoformat(start[:10]) if start else None
    except ValueError:
        start_on = None
    total = max(1, (paid_until - start_on).days + 1) if start_on and start_on <= paid_until else max(1, days)
    progress = round(min(100, 100 * days / total))
    return {"lifetime": False, "end": paid_until, "days": days, "progress": progress}


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
            SELECT c.id AS client_id, c.period, c.start_date, c.end_date,
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
    details = subscription_display(row["period"], row["start_date"], row["end_date"])
    if details["lifetime"]:
        subscription = '<div class="plan unlimited"><span class="infinity">∞</span><div><strong>Подписка: Навсегда</strong><small>Безлимитное подключение</small></div></div>'
    elif details["end"] is None:
        subscription = '<div class="plan">Срок подписки уточняется</div>'
    else:
        days = details["days"]
        progress = details["progress"]
        day_label = "день" if days % 10 == 1 and days % 100 != 11 else "дня" if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14) else "дней"
        counter = f"{days} {day_label} осталось" if days else "Подписка закончилась"
        expiry = details["end"].strftime("%d.%m.%Y")
        subscription = (f'<div class="plan"><div class="plan-header"><strong>{counter}</strong>'
                        f'<span>до {expiry}</span></div>'
                        f'<div class="progress" role="progressbar" aria-label="Остаток подписки" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{progress}">'
                        f'<div class="progress-fill" style="width:{progress}%"></div></div></div>')
    mobile_sub = f"https://{DOMAIN}/sub/{row['phone_token']}"
    pc_sub = f"https://{DOMAIN}/sub/{row['pc_token']}"
    # UXWay already passed owner-side deep-link testing; INCY /import/ is
    # documented by its developers, pending a real Windows owner test.
    def device_state(kind: str) -> tuple[bool, str]:
        if not active:
            return False, "Срок истёк"
        if not row[f"{kind}_enabled"]:
            return False, "Отключён"
        if not row[f"{kind}_deployed"]:
            return False, "Ожидает активации"
        return True, "Доступ разрешён"

    phone_ready, phone_state = device_state("phone")
    pc_ready, pc_state = device_state("pc")
    device_count = int(phone_ready) + int(pc_ready)
    devices = (f'<section class="devices" aria-label="Ваши устройства"><h2>Ваши устройства <span>{device_count} из 2 доступны</span></h2>'
               f'<div class="device"><span>📱 Телефон · UXWay</span><small>{phone_state}</small></div>'
               f'<div class="device"><span>💻 Компьютер · INCY</span><small>{pc_state}</small></div></section>')
    links = ""
    if phone_ready:
        links += (f'<a class="primary" href="{escape("uxway://add/" + mobile_sub, quote=True)}">'
                  '📱 Подключить телефон · UXWay</a>')
    if pc_ready:
        links += (f'<a class="secondary" href="{escape("incy://import/" + pc_sub, quote=True)}">'
                  '💻 Подключить компьютер · INCY</a>')
    if not links:
        links = '<p class="notice">Подключение неактивно. Проверьте срок подписки или обратитесь к администратору.</p>'
    downloads = (f'<div class="downloads"><div class="downloads-title">Нет приложения? Установите:</div>'
                 f'<div class="download-links"><a href="{UXWAY_IOS}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">UXWay · iPhone</a>'
                 f'<a href="{UXWAY_ANDROID}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">UXWay · Android</a>'
                 f'<a href="{INCY_WINDOWS}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">INCY · Windows</a>'
                 '</div></div>')
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>CAESAR VPN — Подключение</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#10131a;color:#f7f8fd;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;padding:24px}}
main{{width:min(100%,480px);padding:34px 28px;border:1px solid #30394b;border-radius:22px;background:linear-gradient(150deg,#1c2433,#141821);box-shadow:0 18px 55px #0006}}
h1{{margin:0;font-size:32px;letter-spacing:1px}}.tag{{margin-top:10px;color:#9dadc6;font-size:14px}}.client-number{{display:inline-flex;align-items:center;gap:6px;margin-top:13px;padding:7px 11px;border-radius:9px;background:#2b3d59;color:#dbe8ff;font-size:13px}}.client-number strong{{color:#fff;font-size:15px}}.plan{{margin:26px 0 22px;padding:17px 16px;border:1px solid #3b4a60;border-radius:12px;background:#202a3a;color:#d7e3f4}}
.unlimited{{display:flex;align-items:center;gap:15px}}.unlimited strong{{display:block;font-size:17px}}.unlimited small{{display:block;margin-top:4px;color:#9dadc6}}.infinity{{font-size:36px;color:#84a6ff;line-height:1}}
.plan-header{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}}.plan-header span{{font-size:13px;color:#9dadc6}}.progress{{height:9px;margin-top:15px;background:#11192a;border-radius:999px;overflow:hidden}}.progress-fill{{height:100%;border-radius:inherit;background:linear-gradient(90deg,#5c88ff,#91a8ff)}}
.devices{{margin:0 0 22px}}.devices h2{{display:flex;justify-content:space-between;gap:8px;margin:0 0 11px;font-size:15px}}.devices h2 span{{font-size:12px;font-weight:500;color:#9dadc6}}.device{{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 0;border-top:1px solid #30394b;font-size:13px}}.device small{{font-size:12px;white-space:nowrap;color:#9cb9ec}}
a{{display:block;text-align:center;margin:11px 0;padding:15px 12px;border-radius:12px;text-decoration:none;font-weight:700;transition:filter .15s}}a:hover{{filter:brightness(1.15)}}.primary{{background:#5c88ff;color:#fff}}.secondary{{background:#2b3d59;color:#f7f8fd;border:1px solid #5471a4}}
.downloads{{margin-top:24px;padding-top:18px;border-top:1px solid #30394b}}.downloads-title{{font-size:13px;color:#9dadc6}}.download-links{{display:flex;flex-wrap:wrap;column-gap:16px}}.download-links a{{display:inline-block;margin:6px 0;padding:3px 0;font-size:12px;font-weight:500;color:#9bb9ff;text-decoration:underline;text-underline-offset:3px}}
.foot{{margin:22px 0 0;font-size:13px;line-height:1.6;color:#91a1bd}}.foot span{{display:block;margin-top:4px}}.foot span:first-child{{margin-top:0}}.notice{{padding:14px;border-radius:12px;background:#493634;color:#ffe1db}}
@media (max-width:600px){{body{{display:block;min-height:100svh;padding:28px 18px 36px}}main{{margin:0 auto}}.device{{align-items:flex-start;flex-wrap:wrap}}}}
</style></head><body><main><h1>CAESAR VPN</h1><div class="tag">Ваше персональное подключение</div>
<div class="client-number">Ваш номер клиента: <strong>№{int(row['client_id'])}</strong></div>
{subscription}{devices}{links}{downloads}
<p class="foot"><span>Для телефона используйте UXWay, для компьютера — INCY.</span><span>У каждого устройства свой ключ.</span><span>Никому не пересылайте эту страницу.</span></p>
</main></body></html>"""


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "")
    headers = [("Content-Type", "text/plain; charset=utf-8"),
               ("Cache-Control", "no-store, private"),
               ("Referrer-Policy", "no-referrer"),
               ("X-Frame-Options", "DENY"),
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
