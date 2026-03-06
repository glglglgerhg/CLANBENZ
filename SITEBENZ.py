import gzip
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('manage.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SERVER_PORT = int(os.getenv("PORT", "8080"))
DB_NAME = "clan_benz.db"
MAIN_ADMIN_USERNAME = "admin"
MAIN_ADMIN_PASSWORD = "Gotlib2010"
SESSION_TTL_HOURS = 12

DEFAULT_GALLERY_IMAGES = [
    "https://i.postimg.cc/tRvPcHPQ/1image.png",
    "https://i.postimg.cc/xjpHZSHv/2025-07-06-21-26-23.png",
    "https://i.postimg.cc/wx4JrdJR/252490-54.jpg",
    "https://i.postimg.cc/xjpHZSHT/254aaf27-a461-4e47-a718-0185eda4dbf5.jpg",
    "https://i.postimg.cc/tC1PQmtx/2image.png",
    "https://i.postimg.cc/XN1FP0FY/460c3542-8aac-49c3-a0fc-a03e3fae36db.jpg",
    "https://i.postimg.cc/zD2WQ1WD/74e3def6-5f5d-4da9-9ed0-17ca000cdb3c.jpg",
]

SESSIONS = {}
SERVER_STARTED_AT = time.time()


def db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db():
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname TEXT NOT NULL,
                steam_id TEXT NOT NULL,
                playtime INTEGER NOT NULL,
                discord TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'new'
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                user_agent TEXT,
                path TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_super INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS gallery_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                added_by TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'approved',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_by TEXT,
                reviewed_at DATETIME
            )
            """
        )
        conn.commit()

        cur.execute("SELECT id FROM admins WHERE username = ?", (MAIN_ADMIN_USERNAME,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO admins (username, password, is_super, created_by) VALUES (?, ?, 1, ?)",
                (MAIN_ADMIN_USERNAME, MAIN_ADMIN_PASSWORD, MAIN_ADMIN_USERNAME),
            )

        cur.execute("SELECT COUNT(*) AS cnt FROM gallery_images")
        if cur.fetchone()["cnt"] == 0:
            cur.executemany(
                "INSERT INTO gallery_images (url, added_by, status, reviewed_by, reviewed_at) VALUES (?, ?, 'approved', ?, ?)",
                [(url, MAIN_ADMIN_USERNAME, MAIN_ADMIN_USERNAME, datetime.now().isoformat()) for url in DEFAULT_GALLERY_IMAGES],
            )
        conn.commit()


def parse_cookies(cookie_header):
    cookies = {}
    if not cookie_header:
        return cookies
    for item in cookie_header.split(';'):
        if '=' in item:
            key, value = item.strip().split('=', 1)
            cookies[key] = value
    return cookies


def create_session(username, is_super):
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = {
        "username": username,
        "is_super": bool(is_super),
        "expires": datetime.now() + timedelta(hours=SESSION_TTL_HOURS),
    }
    return sid


def get_session(cookie_header):
    sid = parse_cookies(cookie_header).get("admin_session")
    if not sid or sid not in SESSIONS:
        return None
    session = SESSIONS[sid]
    if datetime.now() > session["expires"]:
        del SESSIONS[sid]
        return None
    session["expires"] = datetime.now() + timedelta(hours=SESSION_TTL_HOURS)
    return sid, session


def json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


class ClanRequestHandler(BaseHTTPRequestHandler):
    server_version = "ClanBenz/2.0"

    def _send(self, code, data, content_type="text/html; charset=utf-8"):
        accept = self.headers.get("Accept-Encoding", "")
        raw = data if isinstance(data, bytes) else data.encode("utf-8")
        use_gzip = "gzip" in accept and len(raw) > 700

        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=60")
        self.send_header("X-Content-Type-Options", "nosniff")
        if use_gzip:
            buff = BytesIO()
            with gzip.GzipFile(fileobj=buff, mode='wb') as gz:
                gz.write(raw)
            raw = buff.getvalue()
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record_visit(self, path):
        try:
            with db_connection() as conn:
                conn.execute(
                    "INSERT INTO visits (ip_address, user_agent, path) VALUES (?, ?, ?)",
                    (self.client_address[0], self.headers.get("User-Agent", ""), path),
                )
                conn.commit()
        except Exception as exc:
            logger.error("Visit save error: %s", exc)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _require_admin(self):
        auth = get_session(self.headers.get("Cookie", ""))
        if not auth:
            self._send(401, json_bytes({"error": "unauthorized"}), "application/json; charset=utf-8")
            return None
        return auth

    def do_GET(self):
        path = urlparse(self.path).path
        self._record_visit(path)

        if path == '/':
            return self._send(200, self.home_html())
        if path == '/zayavka':
            return self._send(200, self.application_html())
        if path == '/gallery-images':
            with db_connection() as conn:
                rows = conn.execute("SELECT url FROM gallery_images WHERE status='approved' ORDER BY id DESC").fetchall()
            return self._send(200, json_bytes({"images": [r["url"] for r in rows]}), "application/json; charset=utf-8")

        if path == '/admin' or path == '/admin/':
            auth = get_session(self.headers.get("Cookie", ""))
            if not auth:
                self.send_response(302)
                self.send_header("Location", "/admin/login")
                self.end_headers()
                return
            _, session = auth
            return self._send(200, self.admin_html(session))

        if path == '/admin/login':
            return self._send(200, self.admin_login_html())

        if path == '/admin/logout':
            auth = get_session(self.headers.get("Cookie", ""))
            if auth:
                sid, _ = auth
                SESSIONS.pop(sid, None)
            self.send_response(302)
            self.send_header("Set-Cookie", "admin_session=; Max-Age=0; Path=/; HttpOnly")
            self.send_header("Location", "/admin/login")
            self.end_headers()
            return

        if path == '/admin/api/stats':
            auth = self._require_admin()
            if not auth:
                return
            return self._send(200, json_bytes(self.collect_stats()), "application/json; charset=utf-8")

        if path == '/admin/api/applications':
            auth = self._require_admin()
            if not auth:
                return
            with db_connection() as conn:
                rows = conn.execute("SELECT * FROM applications ORDER BY timestamp DESC LIMIT 100").fetchall()
            return self._send(200, json_bytes({"applications": [dict(r) for r in rows]}), "application/json; charset=utf-8")

        if path == '/admin/api/gallery/pending':
            auth = self._require_admin()
            if not auth:
                return
            _, session = auth
            if not session["is_super"]:
                return self._send(403, json_bytes({"error": "forbidden"}), "application/json; charset=utf-8")
            with db_connection() as conn:
                rows = conn.execute("SELECT * FROM gallery_images WHERE status='pending' ORDER BY created_at DESC").fetchall()
            return self._send(200, json_bytes({"pending": [dict(r) for r in rows]}), "application/json; charset=utf-8")

        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/submit_application':
            data = self._read_json()
            required = ["nickname", "steamId", "playtime", "discord", "role", "message"]
            if any(not str(data.get(k, '')).strip() for k in required):
                return self._send(400, json_bytes({"error": "Заполните все поля"}), "application/json; charset=utf-8")
            with db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO applications (nickname, steam_id, playtime, discord, role, message, ip_address)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["nickname"],
                        data["steamId"],
                        int(data["playtime"]),
                        data["discord"],
                        data["role"],
                        data["message"],
                        self.client_address[0],
                    ),
                )
                conn.commit()
            return self._send(200, json_bytes({"success": True}), "application/json; charset=utf-8")

        if path == '/admin/api/login':
            data = self._read_json()
            username = data.get("username", "").strip()
            password = data.get("password", "").strip()
            with db_connection() as conn:
                row = conn.execute(
                    "SELECT username, is_super FROM admins WHERE username=? AND password=?",
                    (username, password),
                ).fetchone()
            if not row:
                return self._send(401, json_bytes({"error": "Неверные данные"}), "application/json; charset=utf-8")
            sid = create_session(row["username"], row["is_super"])
            self.send_response(200)
            self.send_header("Set-Cookie", f"admin_session={sid}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json_bytes({"success": True, "is_super": bool(row["is_super"])}))
            return

        if path == '/admin/api/add-admin':
            auth = self._require_admin()
            if not auth:
                return
            _, session = auth
            if not session["is_super"]:
                return self._send(403, json_bytes({"error": "Только главный админ"}), "application/json; charset=utf-8")
            data = self._read_json()
            username = data.get("username", "").strip()
            password = data.get("password", "").strip()
            if len(username) < 3 or len(password) < 6:
                return self._send(400, json_bytes({"error": "Минимум 3 символа логин и 6 символов пароль"}), "application/json; charset=utf-8")
            try:
                with db_connection() as conn:
                    conn.execute(
                        "INSERT INTO admins (username, password, is_super, created_by) VALUES (?, ?, 0, ?)",
                        (username, password, session["username"]),
                    )
                    conn.commit()
                return self._send(200, json_bytes({"success": True}), "application/json; charset=utf-8")
            except sqlite3.IntegrityError:
                return self._send(409, json_bytes({"error": "Админ уже существует"}), "application/json; charset=utf-8")

        if path == '/admin/api/gallery/add':
            auth = self._require_admin()
            if not auth:
                return
            _, session = auth
            data = self._read_json()
            url = data.get("url", "").strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                return self._send(400, json_bytes({"error": "Нужна корректная ссылка"}), "application/json; charset=utf-8")
            status = "approved" if session["is_super"] else "pending"
            with db_connection() as conn:
                conn.execute(
                    "INSERT INTO gallery_images (url, added_by, status, reviewed_by, reviewed_at) VALUES (?, ?, ?, ?, ?)",
                    (url, session["username"], status, session["username"] if status == "approved" else None, datetime.now().isoformat() if status == "approved" else None),
                )
                conn.commit()
            msg = "Фото добавлено" if status == "approved" else "Фото отправлено на модерацию главному админу"
            return self._send(200, json_bytes({"success": True, "message": msg}), "application/json; charset=utf-8")

        if path == '/admin/api/gallery/moderate':
            auth = self._require_admin()
            if not auth:
                return
            _, session = auth
            if not session["is_super"]:
                return self._send(403, json_bytes({"error": "Только главный админ"}), "application/json; charset=utf-8")
            data = self._read_json()
            image_id = int(data.get("id", 0))
            action = data.get("action")
            new_status = "approved" if action == "approve" else "rejected"
            with db_connection() as conn:
                conn.execute(
                    "UPDATE gallery_images SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                    (new_status, session["username"], datetime.now().isoformat(), image_id),
                )
                conn.commit()
            return self._send(200, json_bytes({"success": True}), "application/json; charset=utf-8")

        self.send_error(404)

    def collect_stats(self):
        with db_connection() as conn:
            total_visits = conn.execute("SELECT COUNT(*) AS c FROM visits").fetchone()["c"]
            today_visits = conn.execute("SELECT COUNT(*) AS c FROM visits WHERE date(timestamp)=date('now')").fetchone()["c"]
            total_apps = conn.execute("SELECT COUNT(*) AS c FROM applications").fetchone()["c"]
            new_apps = conn.execute("SELECT COUNT(*) AS c FROM applications WHERE status='new'").fetchone()["c"]
            pending_photos = conn.execute("SELECT COUNT(*) AS c FROM gallery_images WHERE status='pending'").fetchone()["c"]
            daily = conn.execute(
                "SELECT date(timestamp) AS day, COUNT(*) AS cnt FROM visits GROUP BY date(timestamp) ORDER BY day DESC LIMIT 7"
            ).fetchall()

        uptime_seconds = int(time.time() - SERVER_STARTED_AT)
        return {
            "total_visits": total_visits,
            "today_visits": today_visits,
            "total_applications": total_apps,
            "new_applications": new_apps,
            "pending_photos": pending_photos,
            "server": {
                "status": "online",
                "uptime_seconds": uptime_seconds,
                "python": os.sys.version.split()[0],
                "platform": "Ubuntu-optimized mode",
            },
            "chart": list(reversed([{"day": r["day"], "count": r["cnt"]} for r in daily])),
        }

    def home_html(self):
        return """
<!DOCTYPE html>
<html lang='ru'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width, initial-scale=1.0'>
<title>Клан BENZ</title>
<style>
body{margin:0;font-family:Arial,sans-serif;background:#0f1115;color:#fff}
header{padding:28px 16px;background:#151923;border-bottom:1px solid #2a3344;text-align:center}
h1{margin:0;font-size:2rem}
.wrap{max-width:1200px;margin:0 auto;padding:24px}
.actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin:16px 0 24px}
.btn{padding:12px 16px;border-radius:10px;text-decoration:none;color:#fff;background:#2563eb}
.btn.alt{background:#334155}
.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.gallery img{width:100%;height:200px;object-fit:cover;border-radius:12px;border:1px solid #273045}
footer{text-align:center;padding:20px;color:#94a3b8}
</style>
</head>
<body>
<header><h1>Клан BENZ</h1></header>
<div class='wrap'>
  <div class='actions'>
    <a class='btn' href='/zayavka'>Подать заявку</a>
    <a class='btn alt' href='/admin'>Админка</a>
  </div>
  <h2>Галерея клана</h2>
  <div class='gallery' id='gallery'></div>
</div>
<footer>BENZ © 2026</footer>
<script>
fetch('/gallery-images').then(r=>r.json()).then(data=>{
  const g=document.getElementById('gallery');
  data.images.forEach((url,i)=>{
    const img=document.createElement('img');
    img.src=url; img.alt='Фото клана '+(i+1); img.loading='lazy';
    g.appendChild(img);
  });
});
</script>
</body>
</html>
"""

    def application_html(self):
        return """
<!DOCTYPE html><html lang='ru'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Заявка в клан BENZ</title>
<style>body{font-family:Arial;background:#0f1115;color:#fff;margin:0}.wrap{max-width:720px;margin:30px auto;padding:20px}input,textarea{width:100%;padding:10px;margin:8px 0;border-radius:8px;border:1px solid #334155;background:#111827;color:#fff}.btn{padding:12px 16px;background:#2563eb;border:0;color:#fff;border-radius:10px;cursor:pointer}.ok{color:#22c55e}.er{color:#ef4444}</style>
</head><body><div class='wrap'><h1>Заявка в клан BENZ</h1>
<input id='nickname' placeholder='Никнейм'>
<input id='steamId' placeholder='Steam ID'>
<input id='playtime' type='number' placeholder='Часы в игре'>
<input id='discord' placeholder='Discord'>
<input id='role' placeholder='Роль в клане'>
<textarea id='message' rows='5' placeholder='Почему хотите в клан?'></textarea>
<button class='btn' onclick='send()'>Отправить</button>
<p id='msg'></p><a href='/' style='color:#93c5fd'>← На главную</a></div>
<script>
async function send(){
const payload={nickname:nickname.value,steamId:steamId.value,playtime:playtime.value,discord:discord.value,role:role.value,message:message.value};
const res=await fetch('/submit_application',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
const data=await res.json();
msg.textContent=data.success?'Заявка отправлена!':(data.error||'Ошибка');
msg.className=data.success?'ok':'er';
}
</script></body></html>
"""

    def admin_login_html(self):
        return """
<!DOCTYPE html><html lang='ru'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Вход в админку</title><style>body{font-family:Arial;background:#0f172a;color:#fff;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}.card{background:#111827;padding:24px;border-radius:12px;width:320px}input{width:100%;padding:10px;margin:8px 0;border-radius:8px;border:1px solid #334155;background:#0b1220;color:#fff}.btn{width:100%;padding:10px;border:0;border-radius:8px;background:#2563eb;color:#fff}.hint{font-size:12px;color:#93c5fd}</style>
</head><body><div class='card'><h2>Админка BENZ</h2><p class='hint'>Главный админ: admin / Gotlib2010</p>
<input id='username' placeholder='Логин'><input id='password' placeholder='Пароль' type='password'><button class='btn' onclick='login()'>Войти</button><p id='m'></p><a href='/' style='color:#93c5fd'>← На сайт</a></div>
<script>
async function login(){const r=await fetch('/admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:username.value,password:password.value})});
const d=await r.json();if(d.success){location.href='/admin'}else{m.textContent=d.error||'Ошибка';}}
</script></body></html>
"""

    def admin_html(self, session):
        is_super = 'true' if session['is_super'] else 'false'
        return f"""
<!DOCTYPE html><html lang='ru'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Админка BENZ</title>
<script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
<style>
body{{font-family:Arial;background:#0b1220;color:#fff;margin:0}}
.wrap{{max-width:1240px;margin:0 auto;padding:20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}}
.card{{background:#111827;padding:14px;border-radius:12px;border:1px solid #243146}}
input{{padding:10px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#fff}}
button{{padding:10px 12px;border:0;border-radius:8px;background:#2563eb;color:#fff;cursor:pointer}}
.row{{display:flex;gap:8px;flex-wrap:wrap}}
.pending{{background:#3f1d1d;padding:10px;border-radius:10px;margin:8px 0}}
.ok{{color:#22c55e}} .warn{{color:#facc15}}
</style></head><body><div class='wrap'>
<div class='row' style='justify-content:space-between'><h1>Админ-панель BENZ</h1><a href='/admin/logout' style='color:#93c5fd'>Выйти</a></div>
<div id='serverBox' class='card'>Загрузка статуса сервера...</div>
<canvas id='chart' height='90'></canvas>
<h2>Ключевая статистика (только для админов)</h2>
<div class='grid' id='stats'></div>

<div class='card' style='margin-top:14px'><h3>Добавить фото на главную</h3>
<div class='row'><input id='photoUrl' style='flex:1' placeholder='https://...'><button onclick='addPhoto()'>Добавить</button></div>
<p id='photoMsg'></p></div>

<div class='card' style='margin-top:14px'><h3>Заявки</h3><div id='apps'></div></div>

<div class='card' id='superTools' style='margin-top:14px;display:none'>
<h3>Главный админ: управление</h3>
<div class='row'><input id='newAdminUser' placeholder='Логин'><input id='newAdminPass' placeholder='Пароль'><button onclick='addAdmin()'>Добавить админа</button></div>
<p id='adminMsg'></p>
<h4>Модерация фото</h4>
<div id='pending'></div>
</div>

</div>
<script>
const IS_SUPER={is_super};
let chart;
async function loadStats(){{
 const r=await fetch('/admin/api/stats'); const d=await r.json();
 stats.innerHTML=`<div class='card'><b>Всего посещений</b><div>${{d.total_visits}}</div></div>
 <div class='card'><b>За сегодня</b><div>${{d.today_visits}}</div></div>
 <div class='card'><b>Заявок</b><div>${{d.total_applications}}</div></div>
 <div class='card'><b>Новых заявок</b><div>${{d.new_applications}}</div></div>
 <div class='card'><b>Ожидают модерации фото</b><div>${{d.pending_photos}}</div></div>`;
 serverBox.innerHTML=`<b>Статус сервера:</b> <span class='ok'>${{d.server.status}}</span> | Uptime: ${{d.server.uptime_seconds}} сек | Python ${{d.server.python}} | ${{d.server.platform}}`;
 const labels=d.chart.map(i=>i.day); const values=d.chart.map(i=>i.count);
 if(chart) chart.destroy();
 chart=new Chart(document.getElementById('chart').getContext('2d'),{{type:'line',data:{{labels,datasets:[{{label:'Посещения по дням',data:values,borderColor:'#60a5fa',backgroundColor:'rgba(96,165,250,0.2)',fill:true,tension:0.35}}]}}}});
}}
async function loadApps(){{
 const r=await fetch('/admin/api/applications'); const d=await r.json();
 apps.innerHTML=d.applications.slice(0,30).map(a=>`<div class='card'><b>${{a.nickname}}</b> (${{a.playtime}} ч) - ${{a.discord}}<br><small>${{a.message}}</small></div>`).join('')||'Нет заявок';
}}
async function addPhoto(){{
 const r=await fetch('/admin/api/gallery/add',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{url:photoUrl.value}})}});
 const d=await r.json(); photoMsg.textContent=d.message||d.error||'Готово';
 photoMsg.className=r.ok?'ok':'warn'; loadStats(); if(IS_SUPER) loadPending();
}}
async function addAdmin(){{
 const r=await fetch('/admin/api/add-admin',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{username:newAdminUser.value,password:newAdminPass.value}})}});
 const d=await r.json(); adminMsg.textContent=d.success?'Админ добавлен':(d.error||'Ошибка');
}}
async function moderate(id,action){{
 await fetch('/admin/api/gallery/moderate',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id,action}})}});
 loadPending(); loadStats();
}}
async function loadPending(){{
 const r=await fetch('/admin/api/gallery/pending'); const d=await r.json();
 pending.innerHTML=(d.pending||[]).map(i=>`<div class='pending'><b>${{i.added_by}}</b>: ${{i.url}}<div class='row'><button onclick='moderate(${{i.id}},"approve")'>Одобрить</button><button onclick='moderate(${{i.id}},"reject")'>Отклонить</button></div></div>`).join('') || 'Нет заявок на модерацию';
}}
if(IS_SUPER){{superTools.style.display='block'; loadPending();}}
loadStats(); loadApps();
</script></body></html>
"""


def run():
    init_db()
    httpd = HTTPServer(('0.0.0.0', SERVER_PORT), ClanRequestHandler)
    logger.info("Сайт запущен: http://localhost:%s", SERVER_PORT)
    httpd.serve_forever()


if __name__ == '__main__':
    run()
