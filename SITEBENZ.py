import logging
import threading
import sqlite3
import requests
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timedelta
import secrets
import os
import time

ps_start_time = time.time()

# Пути к шаблонам и статике
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Настройка логирования с поддержкой Unicode
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('manage.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
SERVER_PORT = 8080
DATABASE_NAME = "clan_benz.db"
MAIN_ADMIN_USERNAME = "main_admin"
MAIN_ADMIN_PASSWORD = "Gotlib2010"

# Глобальные переменные для управления
server_httpd = None
server_thread = None

# Режим технического обслуживания
MAINTENANCE_MODE = False
MAINTENANCE_CONFIG_FILE = "maintenance_mode.json"

# Статистика посещений
visits_db = "visits.db"
visits_count = 0
unique_visitors = set()

# Сессии для админ панели
admin_sessions = {}

ddos_protection_db = "ddos_protection.db"

# Фотографии для галереи
GALLERY_IMAGES = [
    "https://i.postimg.cc/tRvPcHPQ/1image.png",
    "https://i.postimg.cc/xjpHZSHv/2025-07-06-21-26-23.png",
    "https://i.postimg.cc/wx4JrdJR/252490-54.jpg",
    "https://i.postimg.cc/xjpHZSHT/254aaf27-a461-4e47-a718-0185eda4dbf5.jpg",
    "https://i.postimg.cc/tC1PQmtx/2image.png",
    "https://i.postimg.cc/XN1FP0FY/460c3542-8aac-49c3-a0fc-a03e3fae36db.jpg",
    "https://i.postimg.cc/zD2WQ1WD/74e3def6-5f5d-4da9-9ed0-17ca000cdb3c.jpg",
    "https://i.postimg.cc/T2NmsXmy/93446743-461e-40dd-9ae8-3b9d19a0ce38.jpg",
    "https://i.postimg.cc/xjpHZSHH/i3mage.png",
    "https://i.postimg.cc/GhBYwSJB/image.png",
    "https://i.postimg.cc/bNGbcFHS/photo1.jpg"
]


def _normalize_bool(value):
    return bool(value) and str(value).lower() not in ('0', 'false', 'none')


# ==================== РЕЖИМ ТЕХНИЧЕСКОГО ОБСЛУЖИВАНИЯ ====================

def load_maintenance_mode():
    """Загрузка режима обслуживания из файла"""
    global MAINTENANCE_MODE
    try:
        if os.path.exists(MAINTENANCE_CONFIG_FILE):
            with open(MAINTENANCE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                MAINTENANCE_MODE = config.get('maintenance_mode', False)
                logger.info(f"Режим обслуживания загружен: {'ВКЛ' if MAINTENANCE_MODE else 'ВЫКЛ'}")
    except Exception as e:
        logger.error(f"Ошибка загрузки режима обслуживания: {e}")


def save_maintenance_mode(enabled):
    """Сохранение режима обслуживания в файл"""
    global MAINTENANCE_MODE
    try:
        MAINTENANCE_MODE = enabled
        config = {'maintenance_mode': enabled}
        with open(MAINTENANCE_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info(f"Режим обслуживания {'ВКЛЮЧЕН' if enabled else 'ВЫКЛЮЧЕН'}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения режима обслуживания: {e}")
        return False


def get_maintenance_status():
    """Получение статуса режима обслуживания"""
    return MAINTENANCE_MODE


# ==================== ШАБЛОНЫ И СТАТИКА ====================

def read_template(template_name):
    """Загрузка HTML шаблона из папки templates."""
    template_path = os.path.join(TEMPLATES_DIR, template_name)
    with open(template_path, 'r', encoding='utf-8') as file:
        return file.read()


def get_static_file_path(request_path):
    """Безопасно получить путь к статическому файлу."""
    relative_path = request_path.lstrip('/')
    normalized_path = os.path.normpath(relative_path)
    full_path = os.path.join(BASE_DIR, normalized_path)
    if os.path.commonpath([STATIC_DIR, full_path]) != STATIC_DIR:
        return None
    return full_path


# ==================== БАЗА ДАННЫХ ====================

def init_databases():
    """Инициализация всех баз данных"""
    try:
        # Основная база заявок
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
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
        ''')

        # Таблица для отслеживания ограничений по IP
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS application_limits (
                ip_address TEXT PRIMARY KEY,
                last_application_time DATETIME NOT NULL,
                application_count INTEGER DEFAULT 1
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_super_admin INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT DEFAULT 'system'
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gallery_images_custom (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_url TEXT UNIQUE NOT NULL,
                added_by TEXT NOT NULL,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gallery_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_url TEXT UNIQUE NOT NULL,
                submitted_by TEXT NOT NULL,
                submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                reviewed_by TEXT,
                reviewed_at DATETIME,
                review_note TEXT
            )
        ''')
        conn.commit()
        conn.close()
        ensure_main_admin_exists()
        logger.info("Основная база данных инициализирована")

        # База посещений
        conn = sqlite3.connect(visits_db)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                user_agent TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                path TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("База посещений инициализирована")

        # База для защиты от DDoS и ограничения посещений
        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ip_blocks (
                ip_address TEXT PRIMARY KEY,
                block_start_time DATETIME NOT NULL,
                request_count INTEGER DEFAULT 1,
                is_blocked BOOLEAN DEFAULT FALSE,
                block_reason TEXT DEFAULT 'ddos',
                is_manual_block BOOLEAN DEFAULT FALSE,
                block_notes TEXT,
                blocked_by TEXT DEFAULT 'system',
                block_expires DATETIME
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                path TEXT NOT NULL
            )
        ''')

        # Таблица для ручной блокировки IP
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS manual_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                blocked_by TEXT NOT NULL,
                block_reason TEXT,
                block_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE,
                expires_at DATETIME
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("База защиты от DDoS и ограничения посещений инициализирована")

    except Exception as e:
        logger.error(f"Ошибка инициализации баз данных: {e}")


def is_ip_manually_blocked(ip_address):
    """Проверяет, заблокирован ли IP вручную"""
    try:
        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, block_reason, block_time, expires_at 
            FROM manual_blocks 
            WHERE ip_address = ? AND is_active = TRUE
        ''', (ip_address,))

        block = cursor.fetchone()
        conn.close()

        if block:
            block_id, reason, block_time, expires_at = block

            # Проверяем срок действия блокировки
            if expires_at:
                expires_dt = datetime.fromisoformat(expires_at)
                if datetime.now() > expires_dt:
                    # Блокировка истекла, деактивируем её
                    deactivate_manual_block(block_id)
                    return False, None

            return True, {
                'reason': reason,
                'block_time': block_time,
                'expires_at': expires_at
            }

        return False, None

    except Exception as e:
        logger.error(f"Ошибка проверки ручной блокировки IP: {e}")
        return False, None


def deactivate_manual_block(block_id):
    """Деактивирует ручную блокировку"""
    try:
        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE manual_blocks 
            SET is_active = FALSE 
            WHERE id = ?
        ''', (block_id,))

        conn.commit()
        conn.close()
        logger.info(f"Ручная блокировка #{block_id} деактивирована")

    except Exception as e:
        logger.error(f"Ошибка деактивации ручной блокировки: {e}")


def add_manual_block(ip_address, blocked_by, reason=None, expires_hours=None):
    """Добавляет ручную блокировку IP"""
    try:
        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()

        expires_at = None
        if expires_hours:
            expires_at = (datetime.now() + timedelta(hours=expires_hours)).isoformat()

        # Деактивируем старые блокировки для этого IP
        cursor.execute('''
            UPDATE manual_blocks 
            SET is_active = FALSE 
            WHERE ip_address = ? AND is_active = TRUE
        ''', (ip_address,))

        # Добавляем новую блокировку
        cursor.execute('''
            INSERT INTO manual_blocks 
            (ip_address, blocked_by, block_reason, expires_at)
            VALUES (?, ?, ?, ?)
        ''', (ip_address, blocked_by, reason, expires_at))

        # Также обновляем основную таблицу блокировок
        cursor.execute('''
            INSERT OR REPLACE INTO ip_blocks 
            (ip_address, block_start_time, is_blocked, block_reason, is_manual_block, blocked_by, block_expires)
            VALUES (?, ?, TRUE, ?, TRUE, ?, ?)
        ''', (ip_address, datetime.now().isoformat(), f'manual: {reason}', blocked_by, expires_at))

        conn.commit()
        conn.close()

        logger.info(f"IP {ip_address} заблокирован вручную. Причина: {reason}")
        return True

    except Exception as e:
        logger.error(f"Ошибка добавления ручной блокировки: {e}")
        return False


def remove_manual_block(ip_address):
    """Удаляет ручную блокировку IP"""
    try:
        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()

        # Деактивируем ручные блокировки
        cursor.execute('''
            UPDATE manual_blocks 
            SET is_active = FALSE 
            WHERE ip_address = ? AND is_active = TRUE
        ''', (ip_address,))

        # Обновляем основную таблицу блокировок
        cursor.execute('''
            UPDATE ip_blocks 
            SET is_blocked = FALSE, is_manual_block = FALSE
            WHERE ip_address = ? AND is_manual_block = TRUE
        ''', (ip_address,))

        conn.commit()
        conn.close()

        logger.info(f"Ручная блокировка IP {ip_address} снята")
        return True

    except Exception as e:
        logger.error(f"Ошибка снятия ручной блокировки: {e}")
        return False


def get_manual_blocks():
    """Получает список всех активных ручных блокировок"""
    try:
        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, ip_address, blocked_by, block_reason, block_time, expires_at
            FROM manual_blocks 
            WHERE is_active = TRUE
            ORDER BY block_time DESC
        ''')

        blocks = []
        for row in cursor.fetchall():
            blocks.append({
                'id': row[0],
                'ip_address': row[1],
                'blocked_by': row[2],
                'reason': row[3],
                'block_time': row[4],
                'expires_at': row[5],
                'is_expired': row[5] and datetime.now() > datetime.fromisoformat(row[5])
            })

        conn.close()
        return blocks

    except Exception as e:
        logger.error(f"Ошибка получения списка ручных блокировок: {e}")
        return []


def check_visit_limit(ip_address, path='/'):
    """Проверка доступа по ручным блокировкам."""
    try:
        is_manual_blocked, block_info = is_ip_manually_blocked(ip_address)
        if is_manual_blocked:
            logger.warning(f"Доступ запрещен: IP {ip_address} заблокирован вручную. Причина: {block_info['reason']}")
            return False, "manual_block"
        return True, "allowed"

    except Exception as e:
        logger.error(f"Ошибка проверки ограничения посещений: {e}")
        return True, "error"


def check_ddos_protection(ip_address):
    """Автоматическая DDoS-защита отключена."""
    return True


def cleanup_old_logs():
    """Очистка старых логов запросов (старше 2 минут)"""
    try:
        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()

        two_minutes_ago = (datetime.now() - timedelta(minutes=2)).isoformat()

        cursor.execute('DELETE FROM request_logs WHERE timestamp < ?', (two_minutes_ago,))

        # Также очищаем разблокированные IP старше 2 минут
        cursor.execute('DELETE FROM ip_blocks WHERE is_blocked = FALSE AND block_start_time < ?',
                       (two_minutes_ago,))

        # Деактивируем просроченные ручные блокировки
        cursor.execute('''
            UPDATE manual_blocks 
            SET is_active = FALSE 
            WHERE expires_at < ? AND is_active = TRUE
        ''', (datetime.now().isoformat(),))

        conn.commit()
        conn.close()

    except Exception as e:
        logger.error(f"Ошибка очистки логов: {e}")


def can_submit_application(ip_address):
    """Проверяет, может ли IP отправить новую заявку (не чаще 1 раза в час)"""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT last_application_time FROM application_limits 
            WHERE ip_address = ?
        ''', (ip_address,))

        result = cursor.fetchone()

        if result:
            last_time = datetime.fromisoformat(result[0])
            time_diff = datetime.now() - last_time
            # Проверяем, прошло ли больше часа
            if time_diff.total_seconds() < 3600:
                logger.info(f"IP {ip_address} пытается отправить заявку раньше чем через час")
                return False

        return True

    except Exception as e:
        logger.error(f"Ошибка проверки лимита заявок: {e}")
        return True  # В случае ошибки разрешаем отправку
    finally:
        if conn:
            conn.close()


def update_application_limit(ip_address):
    """Обновляет время последней заявки для IP"""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        current_time = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR REPLACE INTO application_limits 
            (ip_address, last_application_time, application_count)
            VALUES (?, ?, COALESCE((SELECT application_count + 1 FROM application_limits WHERE ip_address = ?), 1))
        ''', (ip_address, current_time, ip_address))

        conn.commit()
        conn.close()
        logger.info(f"Обновлен лимит заявок для IP: {ip_address}")

    except Exception as e:
        logger.error(f"Ошибка обновления лимита заявок: {e}")


def save_application(application_data):
    """Сохранение заявки в базу данных"""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO applications 
            (nickname, steam_id, playtime, discord, role, message, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            application_data['nickname'],
            application_data['steamId'],
            int(application_data['playtime']),  # Преобразуем в int
            application_data['discord'],
            application_data['role'],
            application_data['message'],
            application_data['ip']
        ))
        conn.commit()
        application_id = cursor.lastrowid
        logger.info(f"Заявка #{application_id} сохранена")

        # Обновляем лимит для IP
        update_application_limit(application_data['ip'])

        return application_id
    except sqlite3.Error as e:
        logger.error(f"Ошибка базы данных при сохранении заявки: {e}")
        if conn:
            conn.rollback()
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при сохранении заявки: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def save_visit(ip, user_agent, path):
    """Сохранение информации о посещении"""
    try:
        conn = sqlite3.connect(visits_db)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO visits (ip_address, user_agent, path)
            VALUES (?, ?, ?)
        ''', (ip, user_agent, path))
        conn.commit()
        conn.close()

        global visits_count, unique_visitors
        visits_count += 1
        unique_visitors.add(ip)

    except Exception as e:
        logger.error(f"Ошибка сохранения посещения: {e}")


def get_visit_stats():
    """Получение статистики посещений"""
    try:
        conn = sqlite3.connect(visits_db)
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM visits')
        total_visits = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(DISTINCT ip_address) FROM visits')
        unique_visitors = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM visits WHERE timestamp >= date("now")')
        today_visits = cursor.fetchone()[0]

        cursor.execute('SELECT path, COUNT(*) FROM visits GROUP BY path ORDER BY COUNT(*) DESC LIMIT 10')
        popular_pages = dict(cursor.fetchall())

        conn.close()

        return {
            'total_visits': total_visits,
            'unique_visitors': unique_visitors,
            'today_visits': today_visits,
            'popular_pages': popular_pages
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики посещений: {e}")
        return {'total_visits': 0, 'unique_visitors': 0, 'today_visits': 0, 'popular_pages': {}}


def get_all_applications():
    """Получение всех заявок"""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM applications ORDER BY timestamp DESC')
        applications = []
        for row in cursor.fetchall():
            applications.append({
                'id': row[0],
                'nickname': row[1],
                'steam_id': row[2],
                'playtime': row[3],
                'discord': row[4],
                'role': row[5],
                'message': row[6],
                'ip_address': row[7],
                'timestamp': row[8],
                'status': row[9] if len(row) > 9 else 'new'
            })
        conn.close()
        return applications
    except Exception as e:
        logger.error(f"Ошибка загрузки заявок: {e}")
        return []


def get_statistics():
    """Получение статистики заявок"""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM applications')
        total_apps = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= date("now")')
        today_apps = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= datetime("now", "-7 days")')
        week_apps = cursor.fetchone()[0]

        cursor.execute('SELECT role, COUNT(*) FROM applications GROUP BY role')
        role_stats = dict(cursor.fetchall())

        conn.close()

        return {
            'total': total_apps,
            'today': today_apps,
            'week': week_apps,
            'roles': role_stats
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return {'total': 0, 'today': 0, 'week': 0, 'roles': {}}


def get_extended_statistics():
    """Получение расширенной статистики"""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        # Основная статистика заявок
        cursor.execute('SELECT COUNT(*) FROM applications')
        total_apps = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= date("now")')
        today_apps = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= datetime("now", "-7 days")')
        week_apps = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= datetime("now", "-1 hour")')
        hour_apps = cursor.fetchone()[0]

        # Статистика по ролям
        cursor.execute('SELECT role, COUNT(*) FROM applications GROUP BY role ORDER BY COUNT(*) DESC')
        role_stats = dict(cursor.fetchall())

        # Статистика по статусам
        cursor.execute('SELECT status, COUNT(*) FROM applications GROUP BY status')
        status_stats = dict(cursor.fetchall())

        # Последние заявки
        cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= datetime("now", "-24 hours")')
        daily_apps = cursor.fetchone()[0]

        # Среднее количество часов
        cursor.execute('SELECT AVG(playtime) FROM applications')
        avg_playtime = cursor.fetchone()[0] or 0

        # Популярные роли
        cursor.execute('SELECT role FROM applications GROUP BY role ORDER BY COUNT(*) DESC LIMIT 1')
        popular_role_result = cursor.fetchone()
        popular_role = popular_role_result[0] if popular_role_result else "Нет данных"

        conn.close()

        return {
            'total': total_apps,
            'today': today_apps,
            'week': week_apps,
            'hour': hour_apps,
            'daily': daily_apps,
            'roles': role_stats,
            'statuses': status_stats,
            'avg_playtime': round(avg_playtime, 1),
            'popular_role': popular_role
        }
    except Exception as e:
        logger.error(f"Ошибка получения расширенной статистики: {e}")
        return {
            'total': 0, 'today': 0, 'week': 0, 'hour': 0, 'daily': 0,
            'roles': {}, 'statuses': {}, 'avg_playtime': 0, 'popular_role': "Нет данных"
        }


def ensure_main_admin_exists():
    """Гарантирует наличие главного администратора."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO admin_users (id, username, password, is_super_admin, created_by)
        VALUES (
            COALESCE((SELECT id FROM admin_users WHERE username = ?), NULL),
            ?, ?, 1, 'system'
        )
    ''', (MAIN_ADMIN_USERNAME, MAIN_ADMIN_USERNAME, MAIN_ADMIN_PASSWORD))
    conn.commit()
    conn.close()


def get_admin_by_credentials(username, password):
    """Возвращает данные админа по логину/паролю."""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, is_super_admin
            FROM admin_users
            WHERE username = ? AND password = ?
        ''', (username, password))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return {
            'username': row[0],
            'is_super_admin': _normalize_bool(row[1])
        }
    except Exception as e:
        logger.error(f"Ошибка проверки учетных данных админа: {e}")
        return None


def get_current_admin(cookie_header):
    """Возвращает текущую админ-сессию или None."""
    if not cookie_header:
        return None

    try:
        cookies = parse_cookies(cookie_header)
        session_id = cookies.get('admin_session')
        session_data = admin_sessions.get(session_id)
        if not session_data:
            return None

        session_time = session_data['last_seen']
        if (datetime.now() - session_time).total_seconds() >= 3600:
            del admin_sessions[session_id]
            return None

        session_data['last_seen'] = datetime.now()
        return session_data
    except Exception as e:
        logger.error(f"Ошибка проверки админ-сессии: {e}")
        return None


def check_admin_auth(cookie_header):
    """Проверка авторизации администратора"""
    return get_current_admin(cookie_header) is not None


def parse_cookies(cookie_header):
    """Парсинг cookies"""
    cookies = {}
    for cookie in cookie_header.split(';'):
        if '=' in cookie:
            key, value = cookie.strip().split('=', 1)
            cookies[key] = value
    return cookies


def create_admin_session(admin_data):
    """Создание новой сессии администратора"""
    session_id = secrets.token_hex(16)
    admin_sessions[session_id] = {
        'username': admin_data['username'],
        'is_super_admin': admin_data['is_super_admin'],
        'last_seen': datetime.now()
    }
    return session_id


def add_admin_user(username, password, created_by):
    """Добавляет нового администратора."""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO admin_users (username, password, is_super_admin, created_by)
            VALUES (?, ?, 0, ?)
        ''', (username, password, created_by))
        conn.commit()
        conn.close()
        return True, "Администратор добавлен"
    except sqlite3.IntegrityError:
        return False, "Администратор с таким логином уже существует"
    except Exception as e:
        logger.error(f"Ошибка добавления администратора: {e}")
        return False, "Внутренняя ошибка"


def add_gallery_image_submission(image_url, submitted_by, is_super_admin=False):
    """Добавляет фото в галерею или на модерацию."""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        if is_super_admin:
            cursor.execute('''
                INSERT OR IGNORE INTO gallery_images_custom (image_url, added_by)
                VALUES (?, ?)
            ''', (image_url, submitted_by))
            conn.commit()
            conn.close()
            return True, "Фото добавлено на главный экран"

        cursor.execute('''
            INSERT OR IGNORE INTO gallery_submissions (image_url, submitted_by, status)
            VALUES (?, ?, 'pending')
        ''', (image_url, submitted_by))
        conn.commit()
        conn.close()
        return True, "Фото отправлено на модерацию главному админу"
    except Exception as e:
        logger.error(f"Ошибка добавления фото: {e}")
        return False, "Внутренняя ошибка"


def get_gallery_images():
    """Публичные изображения галереи."""
    images = list(GALLERY_IMAGES)
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT image_url FROM gallery_images_custom
            WHERE is_active = 1
            ORDER BY id DESC
        ''')
        custom = [row[0] for row in cursor.fetchall()]
        conn.close()

        for url in custom:
            if url not in images:
                images.append(url)
        return images
    except Exception as e:
        logger.error(f"Ошибка получения фото галереи: {e}")
        return images


def get_pending_gallery_submissions():
    """Список фото на модерации."""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, image_url, submitted_by, submitted_at
            FROM gallery_submissions
            WHERE status = 'pending'
            ORDER BY submitted_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                'id': row[0],
                'image_url': row[1],
                'submitted_by': row[2],
                'submitted_at': row[3]
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Ошибка получения очереди модерации: {e}")
        return []


def approve_gallery_submission(submission_id, reviewed_by):
    """Одобряет фото из модерации."""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT image_url FROM gallery_submissions
            WHERE id = ? AND status = 'pending'
        ''', (submission_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False, "Заявка не найдена"

        image_url = row[0]
        cursor.execute('''
            INSERT OR IGNORE INTO gallery_images_custom (image_url, added_by)
            VALUES (?, ?)
        ''', (image_url, reviewed_by))
        cursor.execute('''
            UPDATE gallery_submissions
            SET status = 'approved', reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
        ''', (reviewed_by, datetime.now().isoformat(), submission_id))
        conn.commit()
        conn.close()
        return True, "Фото одобрено и добавлено"
    except Exception as e:
        logger.error(f"Ошибка одобрения фото: {e}")
        return False, "Внутренняя ошибка"


# ==================== ВЕБ-СЕРВЕР КЛАНА ====================

class ClanRequestHandler(BaseHTTPRequestHandler):

    def _set_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With')
        self.send_header('Access-Control-Allow-Credentials', 'true')

    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def serve_static_file(self, path):
        """Отдача статических файлов."""
        file_path = get_static_file_path(path)
        if not file_path or not os.path.exists(file_path):
            self.send_error(404)
            return

        content_types = {
            '.css': 'text/css; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.svg': 'image/svg+xml',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }

        _, ext = os.path.splitext(file_path)
        content_type = content_types.get(ext.lower(), 'application/octet-stream')

        try:
            with open(file_path, 'rb') as file:
                content = file.read()
            self.send_response(200)
            self.send_header('Content-type', content_type)
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.error(f"Ошибка отдачи статического файла {file_path}: {e}")
            self.send_error(500)

    def _check_protection(self):
        """Проверка ручной блокировки IP."""
        ip_address = self.client_address[0]

        # Очищаем старые логи раз в 20 запросов (для оптимизации)
        if hash(ip_address) % 20 == 0:
            cleanup_old_logs()

        # Проверяем только ручные блокировки
        visit_allowed, visit_reason = check_visit_limit(ip_address, self.path)

        if not visit_allowed:
            if visit_reason == "manual_block":
                self._send_manual_block_error(ip_address)
                return False
            else:
                self._send_ddos_error(ip_address)
                return False

        return True

    def _send_manual_block_error(self, ip_address):
        """Отправка ошибки ручной блокировки"""
        self.send_response(403)  # Forbidden
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Доступ запрещен</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: Arial, sans-serif; 
                    background: #1a1a1a; 
                    color: white; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    min-height: 100vh; 
                    margin: 0; 
                    padding: 20px;
                }}
                .container {{ 
                    background: #2a2a2a; 
                    padding: 3rem; 
                    border-radius: 15px; 
                    text-align: center; 
                    border: 2px solid #ff4444; 
                    max-width: 500px;
                    box-shadow: 0 10px 30px rgba(255, 68, 68, 0.3);
                }}
                h1 {{ 
                    color: #ff4444; 
                    margin-bottom: 1rem;
                    font-size: 2rem;
                }}
                .icon {{
                    font-size: 4rem;
                    margin-bottom: 1rem;
                }}
                .info {{
                    background: #333;
                    padding: 1rem;
                    border-radius: 8px;
                    margin: 1.5rem 0;
                    border-left: 4px solid #ff9900;
                }}
                .btn {{
                    background: #666;
                    color: white;
                    padding: 0.8rem 1.5rem;
                    text-decoration: none;
                    border-radius: 5px;
                    font-weight: bold;
                    display: inline-block;
                    margin-top: 1rem;
                    transition: background 0.3s;
                }}
                .btn:hover {{
                    background: #777;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">🚫</div>
                <h1>Доступ запрещен</h1>
                <p>Ваш IP-адрес был заблокирован администратором.</p>

                <div class="info">
                    <p><strong>Заблокированный IP:</strong> {ip_address}</p>
                    <p><strong>Статус:</strong> постоянная блокировка</p>
                </div>

                <p>Если вы считаете, что это ошибка, свяжитесь с администратором сайта.</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(error_html.encode('utf-8'))

    def _send_visit_limit_error(self, ip_address):
        """Совместимый ответ, если ограничение трафика включат в будущем."""
        self.send_response(429)  # Too Many Requests
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Слишком много запросов</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: Arial, sans-serif; 
                    background: #1a1a1a; 
                    color: white; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    min-height: 100vh; 
                    margin: 0; 
                    padding: 20px;
                }}
                .container {{ 
                    background: #2a2a2a; 
                    padding: 3rem; 
                    border-radius: 15px; 
                    text-align: center; 
                    border: 2px solid #ff4444; 
                    max-width: 500px;
                    box-shadow: 0 10px 30px rgba(255, 68, 68, 0.3);
                }}
                h1 {{ 
                    color: #ff4444; 
                    margin-bottom: 1rem;
                    font-size: 2rem;
                }}
                .icon {{
                    font-size: 4rem;
                    margin-bottom: 1rem;
                }}
                .info {{
                    background: #333;
                    padding: 1rem;
                    border-radius: 8px;
                    margin: 1.5rem 0;
                    border-left: 4px solid #ff9900;
                }}
                .countdown {{
                    font-size: 1.2rem;
                    color: #ff9900;
                    font-weight: bold;
                    margin: 1rem 0;
                }}
                .btn {{
                    background: #666;
                    color: white;
                    padding: 0.8rem 1.5rem;
                    text-decoration: none;
                    border-radius: 5px;
                    font-weight: bold;
                    display: inline-block;
                    margin-top: 1rem;
                    transition: background 0.3s;
                }}
                .btn:hover {{
                    background: #777;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">⏰</div>
                <h1>Слишком много запросов</h1>
                <p>Вы превысили лимит посещений сайта.</p>

                <div class="info">
                    <p><strong>Ограничение:</strong> временно отключено</p>
                    <p><strong>Ваш IP:</strong> {ip_address}</p>
                    <p><strong>Статус:</strong> временно заблокирован</p>
                </div>

                <div class="countdown">
                    ⏳ До разблокировки: 1 минута
                </div>

                <p>Пожалуйста, подождите немного перед следующим посещением.</p>
                <a href="/" class="btn">Попробовать снова</a>
            </div>

            <script>
                // Автоматический редирект через 60 секунд
                setTimeout(function() {{
                    window.location.href = '/';
                }}, 60000);
            </script>
        </body>
        </html>
        """
        self.wfile.write(error_html.encode('utf-8'))

    def _send_ddos_error(self, ip_address):
        """Отправка ошибки DDoS защиты"""
        self.send_response(429)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Too Many Requests</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: Arial, sans-serif; 
                    background: #1a1a1a; 
                    color: white; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    min-height: 100vh; 
                    margin: 0; 
                }}
                .container {{ 
                    background: #2a2a2a; 
                    padding: 2rem; 
                    border-radius: 10px; 
                    text-align: center; 
                    border: 2px solid #ff4444; 
                }}
                h1 {{ color: #ff4444; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Too Many Requests</h1>
                <p>You have exceeded the request limit. Please wait 5 minutes.</p>
                <p><small>IP: {ip_address}</small></p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(error_html.encode('utf-8'))

    def serve_maintenance_page(self):
        """Отображение страницы технического обслуживания"""
        html = """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Техническое обслуживание - Клан BENZ</title>
            <style>
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                }
                body {
                    background: linear-gradient(135deg, #1a1a1a, #2d2d2d);
                    color: #e0e0e0;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    padding: 20px;
                }
                .maintenance-container {
                    background: #2a2a2a;
                    padding: 3rem;
                    border-radius: 15px;
                    text-align: center;
                    border: 2px solid #ff9900;
                    max-width: 600px;
                    box-shadow: 0 20px 40px rgba(0,0,0,0.3);
                }
                .maintenance-icon {
                    font-size: 5rem;
                    margin-bottom: 1.5rem;
                    color: #ff9900;
                }
                h1 {
                    color: #ff9900;
                    margin-bottom: 1rem;
                    font-size: 2.5rem;
                }
                p {
                    font-size: 1.2rem;
                    line-height: 1.6;
                    margin-bottom: 1.5rem;
                    color: #cccccc;
                }
                .admin-login {
                    margin-top: 2rem;
                    padding-top: 1.5rem;
                    border-top: 1px solid #444;
                }
                .admin-btn {
                    background: #666;
                    color: white;
                    padding: 0.8rem 1.5rem;
                    text-decoration: none;
                    border-radius: 5px;
                    font-weight: bold;
                    display: inline-block;
                    transition: background 0.3s;
                }
                .admin-btn:hover {
                    background: #777;
                }
                .status-indicator {
                    display: inline-block;
                    padding: 0.5rem 1rem;
                    background: #e74c3c;
                    color: white;
                    border-radius: 20px;
                    font-size: 0.9rem;
                    font-weight: bold;
                    margin-bottom: 1.5rem;
                }
                .contact-info {
                    background: #333;
                    padding: 1rem;
                    border-radius: 8px;
                    margin: 1.5rem 0;
                    border-left: 4px solid #ff9900;
                }
                @media (max-width: 768px) {
                    .maintenance-container {
                        padding: 2rem;
                    }
                    h1 {
                        font-size: 2rem;
                    }
                    .maintenance-icon {
                        font-size: 4rem;
                    }
                }
            </style>
        </head>
        <body>
            <div class="maintenance-container">
                <div class="maintenance-icon">🔧</div>
                <div class="status-indicator">РЕЖИМ ОБСЛУЖИВАНИЯ</div>
                <h1>Ведутся технические работы</h1>
                <p>Сайт клана BENZ временно недоступен из-за проведения технического обслуживания.</p>

                <div class="contact-info">
                    <p><strong>Мы работаем над улучшением сервиса!</strong></p>
                    <p>Приносим извинения за временные неудобства. Сайт будет доступен в ближайшее время.</p>
                </div>

                <p>Пожалуйста, зайдите позже или свяжитесь с администрацией через Discord.</p>

                <div class="admin-login">
                    <p><small>Для администраторов:</small></p>
                    <a href="/admin" class="admin-btn">Войти в админку</a>
                </div>
            </div>
        </body>
        </html>
        """
        self.send_response(503)  # Service Unavailable
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def do_GET(self):
        # Проверка защиты от DDoS и ограничения посещений
        if not self._check_protection():
            return

        # Проверка режима обслуживания (кроме админки)
        if MAINTENANCE_MODE and not self.path.startswith('/admin'):
            self.serve_maintenance_page()
            return

        # Сохраняем информацию о посещении
        save_visit(self.client_address[0], self.headers.get('User-Agent', ''), self.path)

        parsed_path = urlparse(self.path)
        path = parsed_path.path

        # Маршрутизация запросов
        if path.startswith('/static/'):
            self.serve_static_file(path)
        elif path == '/':
            self.serve_html()
        elif path == '/zayavka':
            self.serve_application_page()
        elif path == '/applications':
            self.serve_applications()
        elif path == '/statistics':
            self.serve_statistics_page()
        elif path == '/api/statistics':
            self.serve_statistics_api()
        elif path == '/gallery-images':
            self.serve_gallery_images()
        elif path == '/rate-limit-status':
            self.serve_rate_limit_status()
        elif path.startswith('/admin'):
            self.handle_admin_request(path)
        else:
            self.send_error(404)

    def do_POST(self):
        # Проверка защиты от DDoS и ограничения посещений
        if not self._check_protection():
            return

        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path == '/submit_application':
            self.handle_application()
        elif path.startswith('/admin'):
            self.handle_admin_post_request(path)
        else:
            self.send_error(404)

    def handle_admin_request(self, path):
        """Обработка запросов админки"""
        if path == '/admin' or path == '/admin/':
            self.serve_admin_page()
        elif path == '/admin/login':
            self.serve_admin_login_page()
        elif path == '/admin/api/stats':
            self.serve_admin_api_stats()
        elif path == '/admin/api/applications':
            self.serve_admin_applications()
        elif path == '/admin/api/manual-blocks':
            self.serve_admin_manual_blocks()
        elif path == '/admin/api/server-status':
            self.serve_admin_server_status()
        elif path == '/admin/api/gallery/pending':
            self.serve_pending_gallery_submissions()
        elif path == '/admin/logout':
            self.handle_admin_logout()
        else:
            self.send_error(404)

    def handle_admin_post_request(self, path):
        """Обработка POST запросов админки"""
        if path == '/admin/api/login':
            self.handle_admin_login()
        elif path == '/admin/api/manual-blocks/add':
            self.handle_admin_add_manual_block()
        elif path == '/admin/api/manual-blocks/remove':
            self.handle_admin_remove_manual_block()
        elif path == '/admin/api/maintenance/toggle':
            self.handle_maintenance_toggle()
        elif path == '/admin/api/admins/add':
            self.handle_add_admin_user()
        elif path == '/admin/api/gallery/add':
            self.handle_add_gallery_image()
        elif path == '/admin/api/gallery/approve':
            self.handle_approve_gallery_submission()
        else:
            self.send_error(404)

    def handle_maintenance_toggle(self):
        """Включение/выключение режима обслуживания"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.send_error(403)
            return

        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            enabled = data.get('enabled', False)
            success = save_maintenance_mode(enabled)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': success}).encode())

        except Exception as e:
            logger.error(f"Ошибка переключения режима обслуживания: {e}")
            self.send_error(500)

    def serve_html(self):
        """Отдача HTML страницы клана"""
        try:
            html_content = self.get_html_content()
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
        except Exception as e:
            logger.error(f"Error serving HTML: {e}")
            self.send_error(500)

    def serve_application_page(self):
        """Отдача страницы с формой заявки"""
        try:
            # Проверяем, может ли пользователь отправить заявку
            ip_address = self.client_address[0]
            can_submit = can_submit_application(ip_address)

            html_content = self.get_application_page_content(can_submit)
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
        except Exception as e:
            logger.error(f"Error serving application page: {e}")
            self.send_error(500)

    def get_html_content(self):
        """Генерация HTML контента для главной страницы"""
        return read_template('index.html')

    def get_application_page_content(self, can_submit):
        """Генерация HTML контента для страницы заявки"""
        if not can_submit:
            return read_template('application_blocked.html')

        return read_template('application_form.html')

    def serve_applications(self):
        """API для получения заявок"""
        try:
            applications = get_all_applications()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            response = {'total': len(applications), 'applications': applications}
            self.wfile.write(json.dumps(response, default=str).encode('utf-8'))
        except Exception as e:
            logger.error(f"Error serving applications: {e}")
            self.send_error(500)

    def serve_statistics_page(self):
        """Отдача страницы статистики только для администраторов"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.redirect_to_admin_login()
            return

        try:
            html_content = read_template('statistics.html')
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
        except Exception as e:
            logger.error(f"Error serving statistics page: {e}")
            self.send_error(500)

    def serve_statistics_api(self):
        """API статистики только для администраторов"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.send_error(403)
            return

        try:
            stats = {
                'applications': get_extended_statistics(),
                'visits': get_visit_stats(),
                'services': {
                    'server': 'Запущен',
                    'server_port': SERVER_PORT,
                    'database': 'Работает'
                },
                'system': {
                    'active_sessions': len(admin_sessions),
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            }
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(stats, default=str).encode('utf-8'))
        except Exception as e:
            logger.error(f"Error serving statistics: {e}")
            self.send_error(500)

    def serve_gallery_images(self):
        """API для получения изображений галереи"""
        try:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(get_gallery_images()).encode('utf-8'))
        except Exception as e:
            logger.error(f"Error serving gallery images: {e}")
            self.send_error(500)

    def serve_rate_limit_status(self):
        """API совместимости: авто-ограничения отключены."""
        try:
            ip_address = self.client_address[0]
            current_time = datetime.now()

            is_blocked, block_info = is_ip_manually_blocked(ip_address)

            status_data = {
                'ip': ip_address,
                'current_requests': 0,
                'limit': None,
                'remaining': None,
                'blocked': is_blocked,
                'block_reason': block_info.get('reason') if is_blocked else None,
                'reset_time': (current_time + timedelta(minutes=1)).isoformat()
            }

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(status_data).encode('utf-8'))

        except Exception as e:
            logger.error(f"Ошибка получения статуса ограничений: {e}")
            self.send_error(500)

    # ==================== АДМИН ПАНЕЛЬ ====================

    def serve_admin_page(self):
        """Главная страница админки"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.redirect_to_admin_login()
            return

        html = self.get_admin_page_content()
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_admin_login_page(self):
        """Страница входа в админку"""
        html = self.get_admin_login_page_content()
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_admin_api_stats(self):
        """API статистики для админки"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.send_error(403)
            return

        stats = {
            'applications': get_extended_statistics(),
            'visits': get_visit_stats(),
            'services': {
                'server': 'Запущен',
                'server_port': SERVER_PORT,
                'database': 'Работает'
            },
            'system': {
                'active_sessions': len(admin_sessions),
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        }
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(stats, default=str).encode('utf-8'))

    def serve_admin_applications(self):
        """API заявок для админки"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.send_error(403)
            return

        applications = get_all_applications()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'applications': applications}, default=str).encode('utf-8'))

    def serve_admin_manual_blocks(self):
        """API блокировок для админки"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.send_error(403)
            return

        try:
            blocks = get_manual_blocks()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'blocks': blocks}).encode('utf-8'))
        except Exception as e:
            logger.error(f"Ошибка получения списка блокировок: {e}")
            self.send_error(500)

    def serve_admin_server_status(self):
        """API статуса сервера для админки"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.send_error(403)
            return

        try:
            uptime = int(time.time() - ps_start_time)
            payload = {
                'server': 'online',
                'port': SERVER_PORT,
                'maintenance': MAINTENANCE_MODE,
                'uptime_seconds': uptime,
                'active_sessions': len(admin_sessions),
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode('utf-8'))
        except Exception as e:
            logger.error(f"Ошибка получения статуса сервера: {e}")
            self.send_error(500)

    def serve_pending_gallery_submissions(self):
        """Список фото на модерации."""
        session = get_current_admin(self.headers.get('Cookie', ''))
        if not session:
            self.send_error(403)
            return

        if not session.get('is_super_admin'):
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'items': []}).encode('utf-8'))
            return

        items = get_pending_gallery_submissions()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'items': items}).encode('utf-8'))

    def handle_admin_login(self):
        """Обработка входа в админку"""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            username = data.get('username', MAIN_ADMIN_USERNAME)
            password = data.get('password', '')
            admin_data = get_admin_by_credentials(username, password)

            if admin_data:
                session_id = create_admin_session(admin_data)
                self.send_response(200)
                self.send_header('Set-Cookie', f'admin_session={session_id}; Path=/; HttpOnly; Max-Age=3600')
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'username': admin_data['username']}).encode())
            else:
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False}).encode())

        except Exception as e:
            logger.error(f"Ошибка входа: {e}")
            self.send_error(500)

    def handle_admin_logout(self):
        """Выход из админки"""
        cookie_header = self.headers.get('Cookie', '')
        cookies = parse_cookies(cookie_header)
        session_id = cookies.get('admin_session')
        if session_id in admin_sessions:
            del admin_sessions[session_id]

        self.send_response(302)
        self.send_header('Set-Cookie', 'admin_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT')
        self.send_header('Location', '/admin/login')
        self.end_headers()

    def handle_add_admin_user(self):
        """Добавление администратора главным админом."""
        session = get_current_admin(self.headers.get('Cookie', ''))
        if not session or not session.get('is_super_admin'):
            self.send_error(403)
            return

        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            username = (data.get('username') or '').strip()
            password = (data.get('password') or '').strip()
            if len(username) < 3 or len(password) < 4:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'message': 'Логин/пароль слишком короткие'}).encode())
                return

            success, message = add_admin_user(username, password, session['username'])
            self.send_response(200 if success else 400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': success, 'message': message}).encode())
        except Exception as e:
            logger.error(f"Ошибка добавления админа: {e}")
            self.send_error(500)

    def handle_add_gallery_image(self):
        """Добавление фото в галерею/на модерацию."""
        session = get_current_admin(self.headers.get('Cookie', ''))
        if not session:
            self.send_error(403)
            return

        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            image_url = (data.get('image_url') or '').strip()
            if not image_url.startswith('http'):
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'message': 'Нужна корректная ссылка на изображение'}).encode())
                return

            success, message = add_gallery_image_submission(
                image_url,
                session['username'],
                session.get('is_super_admin', False)
            )
            self.send_response(200 if success else 400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': success, 'message': message}).encode())
        except Exception as e:
            logger.error(f"Ошибка добавления фото: {e}")
            self.send_error(500)

    def handle_approve_gallery_submission(self):
        """Одобрение фото главным админом."""
        session = get_current_admin(self.headers.get('Cookie', ''))
        if not session or not session.get('is_super_admin'):
            self.send_error(403)
            return

        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            submission_id = int(data.get('submission_id'))

            success, message = approve_gallery_submission(submission_id, session['username'])
            self.send_response(200 if success else 400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': success, 'message': message}).encode())
        except Exception as e:
            logger.error(f"Ошибка одобрения фото: {e}")
            self.send_error(500)

    def handle_admin_add_manual_block(self):
        """Добавление блокировки через админку"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.send_error(403)
            return

        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            ip_address = data.get('ip_address')
            reason = data.get('block_reason')
            expires_hours = data.get('expires_hours')

            if not ip_address:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'message': 'IP-адрес обязателен'}).encode())
                return

            current_admin = get_current_admin(self.headers.get('Cookie', '')) or {'username': 'admin'}
            blocked_by = current_admin['username']
            success = add_manual_block(ip_address, blocked_by, reason, int(expires_hours) if expires_hours else None)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': success}).encode())

        except Exception as e:
            logger.error(f"Ошибка добавления ручной блокировки: {e}")
            self.send_error(500)

    def handle_admin_remove_manual_block(self):
        """Снятие блокировки через админку"""
        if not check_admin_auth(self.headers.get('Cookie', '')):
            self.send_error(403)
            return

        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            ip_address = data.get('ip_address')

            if not ip_address:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'message': 'IP-адрес обязателен'}).encode())
                return

            success = remove_manual_block(ip_address)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': success}).encode())

        except Exception as e:
            logger.error(f"Ошибка снятия ручной блокировки: {e}")
            self.send_error(500)

    def redirect_to_admin_login(self):
        """Перенаправление на страницу логина админки"""
        self.send_response(302)
        self.send_header('Location', '/admin/login')
        self.end_headers()

    def get_admin_page_content(self):
        """Генерация HTML контента для админки"""
        maintenance_status = "ВКЛЮЧЕН" if MAINTENANCE_MODE else "ВЫКЛЮЧЕН"
        maintenance_class = "status status-offline" if MAINTENANCE_MODE else "status status-online"
        admin_session = get_current_admin(self.headers.get('Cookie', '')) or {}
        current_admin = admin_session.get('username', 'admin')
        is_super_admin = admin_session.get('is_super_admin', False)

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Управление кланом BENZ</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #1a1a1a; color: white; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                .header {{ background: #2a2a2a; color: #ff9900; padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #444; position: relative; }}
                .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }}
                .stat-card {{ background: #2a2a2a; padding: 20px; border-radius: 10px; border: 1px solid #444; }}
                .chart-wrap {{ margin-top: 10px; }}
                .chart-row {{ margin-bottom: 10px; }}
                .chart-label {{ font-size: 13px; color: #ccc; margin-bottom: 4px; }}
                .chart-bar-bg {{ height: 10px; background: #333; border-radius: 6px; overflow: hidden; }}
                .chart-bar {{ height: 100%; background: linear-gradient(90deg, #ff9900, #f1c40f); }}
                .control-panel {{ background: #2a2a2a; padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #444; }}
                .btn {{ padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block; }}
                .btn-primary {{ background: #ff9900; color: white; }}
                .btn-danger {{ background: #e74c3c; color: white; }}
                .btn-success {{ background: #27ae60; color: white; }}
                .btn-warning {{ background: #f39c12; color: white; }}
                .status {{ padding: 5px 10px; border-radius: 15px; font-size: 12px; margin-left: 10px; }}
                .status-online {{ background: #27ae60; color: white; }}
                .status-offline {{ background: #e74c3c; color: white; }}
                .logout-btn {{ background: #666; color: white; float: right; }}
                .back-btn {{ background: #444; color: white; float: left; }}
                .maintenance-alert {{ background: #e74c3c; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; border-left: 5px solid #c0392b; }}
                .tab {{ overflow: hidden; border: 1px solid #444; background-color: #2a2a2a; border-radius: 5px; margin-bottom: 20px; }}
                .tab button {{ background-color: inherit; float: left; border: none; outline: none; cursor: pointer; padding: 14px 16px; transition: 0.3s; color: white; font-size: 16px; }}
                .tab button:hover {{ background-color: #333; }}
                .tab button.active {{ background-color: #ff9900; color: black; font-weight: bold; }}
                .tabcontent {{ display: none; padding: 20px 0; }}
                .applications-table {{ width: 100%; border-collapse: collapse; margin-top: 20px; background: #2a2a2a; border-radius: 10px; overflow: hidden; }}
                .applications-table th, .applications-table td {{ padding: 15px; text-align: left; border-bottom: 1px solid #444; }}
                .applications-table th {{ background: #333; color: #ff9900; font-weight: bold; }}
                .applications-table tr:hover {{ background: #333; }}
                .applications-table td {{ color: #e0e0e0; }}
                .message-cell {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
                .message-cell:hover {{ white-space: normal; overflow: visible; }}
                .form-group {{ margin-bottom: 15px; }}
                .form-group label {{ display: block; margin-bottom: 5px; color: #ff9900; font-weight: bold; }}
                .form-group input, .form-group textarea, .form-group select {{ width: 100%; padding: 8px; background: #1a1a1a; border: 1px solid #444; border-radius: 4px; color: white; }}
                .manual-blocks-list {{ margin-top: 20px; }}
                .block-item {{ background: #2a2a2a; padding: 15px; margin-bottom: 10px; border-radius: 5px; border-left: 4px solid #e74c3c; }}
                .block-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
                .block-ip {{ font-weight: bold; color: #ff9900; }}
                .block-reason {{ color: #cccccc; }}
                .block-meta {{ font-size: 12px; color: #999; }}
                .preview-image {{ width: 180px; height: 120px; object-fit: cover; border: 1px solid #555; border-radius: 6px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <a href="/" class="btn back-btn">← Вернуться на сайт</a>
                    <a href="/admin/logout" class="btn logout-btn">Выйти</a>
                    <div style="clear: both;"></div>
                    <h1>Панель управления кланом BENZ</h1>
                    <p>Вы вошли как: <strong>{current_admin}</strong> {'(Главный администратор)' if is_super_admin else '(Администратор)'}</p>
                </div>

                {('<div class="maintenance-alert"><strong>⚠️ ВНИМАНИЕ:</strong> Режим технического обслуживания ВКЛЮЧЕН. Все пользователи видят страницу обслуживания.</div>' if MAINTENANCE_MODE else '')}

                <div class="tab">
                    <button class="tablinks active" onclick="openTab(event, 'Dashboard')">Дашборд</button>
                    <button class="tablinks" onclick="openTab(event, 'Applications')">Заявки</button>
                    <button class="tablinks" onclick="openTab(event, 'IPBlocks')">Блокировка IP</button>
                    <button class="tablinks" onclick="openTab(event, 'Media')">Галерея</button>
                    {'<button class="tablinks" onclick="openTab(event, &quot;SuperAdmin&quot;)">Главный админ</button>' if is_super_admin else ''}
                    <button class="tablinks" onclick="openTab(event, 'Maintenance')">Тех. обслуживание</button>
                </div>

                <div id="Dashboard" class="tabcontent" style="display: block;">
                    <div class="stats-grid" id="statsGrid"></div>
                    <div class="control-panel">
                        <h2>Статус сервера</h2>
                        <div id="serverStatusPanel">Загрузка...</div>
                    </div>
                    <div class="control-panel">
                        <h2>График ролей</h2>
                        <div id="rolesChart" class="chart-wrap">Загрузка...</div>
                    </div>
                </div>

                <div id="Applications" class="tabcontent">
                    <h2>Заявки на вступление</h2>
                    <div id="applicationsList"></div>
                </div>

                <div id="IPBlocks" class="tabcontent">
                    <h2>Управление блокировками IP</h2>
                    <div class="control-panel">
                        <h3>Добавить блокировку</h3>
                        <form id="blockIpForm">
                            <div class="form-group">
                                <label for="ip_address">IP-адрес:</label>
                                <input type="text" id="ip_address" name="ip_address" required placeholder="Например: 192.168.1.1">
                            </div>
                            <div class="form-group">
                                <label for="block_reason">Причина блокировки:</label>
                                <textarea id="block_reason" name="block_reason" rows="3" placeholder="Причина блокировки..."></textarea>
                            </div>
                            <div class="form-group">
                                <label for="expires_hours">Срок блокировки (часы):</label>
                                <select id="expires_hours" name="expires_hours">
                                    <option value="">Навсегда</option>
                                    <option value="1">1 час</option>
                                    <option value="24">24 часа</option>
                                    <option value="168">1 неделя</option>
                                    <option value="720">1 месяц</option>
                                </select>
                            </div>
                            <button type="submit" class="btn btn-danger">Заблокировать IP</button>
                        </form>
                    </div>
                    <div class="manual-blocks-list">
                        <h3>Активные блокировки</h3>
                        <div id="manualBlocksList"></div>
                    </div>
                </div>

                <div id="Media" class="tabcontent">
                    <h2>Управление галереей</h2>
                    <div class="control-panel">
                        <form id="addImageForm">
                            <div class="form-group">
                                <label for="image_url">Ссылка на фото:</label>
                                <input type="url" id="image_url" required placeholder="https://...">
                            </div>
                            <button type="submit" class="btn btn-primary">Добавить фото</button>
                        </form>
                        <p>{'Главный админ добавляет фото сразу на главный экран.' if is_super_admin else 'Ваши фото отправляются на модерацию главному админу.'}</p>
                    </div>
                    <div id="pendingGalleryWrap" class="control-panel" style="display:{'block' if is_super_admin else 'none'};">
                        <h3>Фото на модерации</h3>
                        <div id="pendingGalleryList"></div>
                    </div>
                </div>

                {'<div id="SuperAdmin" class="tabcontent"><h2>Управление администраторами</h2><div class="control-panel"><form id="addAdminForm"><div class="form-group"><label for="new_admin_username">Логин нового админа:</label><input id="new_admin_username" required></div><div class="form-group"><label for="new_admin_password">Пароль нового админа:</label><input id="new_admin_password" type="password" required></div><button type="submit" class="btn btn-success">Добавить администратора</button></form></div></div>' if is_super_admin else ''}

                <div id="Maintenance" class="tabcontent">
                    <h2>Управление техническим обслуживанием</h2>
                    <div class="control-panel">
                        <h3>Текущий статус</h3>
                        <p>Режим технического обслуживания: <span class="{maintenance_class}">{maintenance_status}</span></p>
                        <button class="btn btn-warning" onclick="toggleMaintenanceMode(true)">Включить режим обслуживания</button>
                        <button class="btn btn-success" onclick="toggleMaintenanceMode(false)">Выключить режим обслуживания</button>
                    </div>
                </div>
            </div>

            <script>
                function openTab(evt, tabName) {{
                    const tabcontent = document.getElementsByClassName('tabcontent');
                    for (let i = 0; i < tabcontent.length; i++) tabcontent[i].style.display = 'none';
                    const tablinks = document.getElementsByClassName('tablinks');
                    for (let i = 0; i < tablinks.length; i++) tablinks[i].className = tablinks[i].className.replace(' active', '');
                    document.getElementById(tabName).style.display = 'block';
                    evt.currentTarget.className += ' active';

                    if (tabName === 'Applications') loadApplications();
                    if (tabName === 'IPBlocks') loadManualBlocks();
                    if (tabName === 'Media') loadPendingGallery();
                }}

                function renderRoleChart(roles) {{
                    const chart = document.getElementById('rolesChart');
                    const entries = Object.entries(roles || {{}});
                    if (!entries.length) {{
                        chart.innerHTML = '<p>Пока нет данных по ролям.</p>';
                        return;
                    }}
                    const max = Math.max(...entries.map(e => e[1]));
                    chart.innerHTML = entries.map(([role, count]) => `
                        <div class="chart-row">
                            <div class="chart-label">${{role}} — ${{count}}</div>
                            <div class="chart-bar-bg"><div class="chart-bar" style="width:${{Math.max(8, (count / max) * 100)}}%"></div></div>
                        </div>
                    `).join('');
                }}

                function updateServerStatus() {{
                    fetch('/admin/api/server-status').then(r => r.json()).then(data => {{
                        const uptimeMinutes = Math.floor(data.uptime_seconds / 60);
                        document.getElementById('serverStatusPanel').innerHTML = `
                            <span class="status status-online">Сервер: Онлайн</span>
                            <span class="status ${{data.maintenance ? 'status-offline' : 'status-online'}}">Обслуживание: ${{data.maintenance ? 'ВКЛ' : 'ВЫКЛ'}}</span>
                            <p>Порт: <strong>${{data.port}}</strong> | Аптайм: <strong>${{uptimeMinutes}} мин</strong> | Активных админ-сессий: <strong>${{data.active_sessions}}</strong></p>
                        `;
                    }});
                }}

                function updateStats() {{
                    fetch('/admin/api/stats').then(r => r.json()).then(data => {{
                        const statsGrid = document.getElementById('statsGrid');
                        statsGrid.innerHTML = `
                            <div class="stat-card"><h3>Заявки всего</h3><p style="font-size:28px">${{data.applications.total}}</p><p>Сегодня: ${{data.applications.today}}</p><p>За неделю: ${{data.applications.week}}</p></div>
                            <div class="stat-card"><h3>Посещения</h3><p style="font-size:28px">${{data.visits.total_visits}}</p><p>Уникальные: ${{data.visits.unique_visitors}}</p></div>
                            <div class="stat-card"><h3>Система</h3><p>Сервер: ${{data.services.server}}</p><p>БД: ${{data.services.database}}</p><p>Обновлено: ${{data.system.timestamp}}</p></div>
                        `;
                        renderRoleChart(data.applications.roles);
                    }});
                    updateServerStatus();
                }}

                function loadApplications() {{
                    fetch('/admin/api/applications').then(r => r.json()).then(data => {{
                        const list = document.getElementById('applicationsList');
                        if (!data.applications.length) {{ list.innerHTML = '<div class="stat-card">Нет заявок</div>'; return; }}
                        let html = '<table class="applications-table"><tr><th>ID</th><th>Ник</th><th>Steam</th><th>Часы</th><th>Discord</th><th>Роль</th><th>Сообщение</th><th>Дата</th></tr>';
                        data.applications.forEach(app => {{
                            html += `<tr><td>${{app.id}}</td><td>${{app.nickname}}</td><td>${{app.steam_id}}</td><td>${{app.playtime}}</td><td>${{app.discord}}</td><td>${{app.role}}</td><td class="message-cell">${{app.message}}</td><td>${{app.timestamp}}</td></tr>`;
                        }});
                        html += '</table>';
                        list.innerHTML = html;
                    }});
                }}

                function loadManualBlocks() {{
                    fetch('/admin/api/manual-blocks').then(r => r.json()).then(data => {{
                        const blocksList = document.getElementById('manualBlocksList');
                        if (data.blocks.length === 0) {{ blocksList.innerHTML = '<div class="stat-card"><p>Нет активных блокировок</p></div>'; return; }}
                        let html = '';
                        data.blocks.forEach(block => {{
                            html += `<div class="block-item"><div class="block-header"><span class="block-ip">${{block.ip_address}}</span><button class="btn btn-success" onclick="unblockIP('${{block.ip_address}}')">Разблокировать</button></div><div class="block-reason">${{block.reason || 'Причина не указана'}}</div><div class="block-meta">Админ: ${{block.blocked_by}}</div></div>`;
                        }});
                        blocksList.innerHTML = html;
                    }});
                }}

                function unblockIP(ipAddress) {{
                    if (!confirm(`Разблокировать IP ${{ipAddress}}?`)) return;
                    fetch('/admin/api/manual-blocks/remove', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ ip_address: ipAddress }}) }})
                    .then(r => r.json()).then(data => {{
                        if (data.success) loadManualBlocks(); else alert('Ошибка разблокировки');
                    }});
                }}

                function toggleMaintenanceMode(enable) {{
                    fetch('/admin/api/maintenance/toggle', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ enabled: enable }}) }})
                    .then(r => r.json()).then(data => {{
                        if (data.success) location.reload(); else alert('Ошибка изменения режима');
                    }});
                }}

                function loadPendingGallery() {{
                    const list = document.getElementById('pendingGalleryList');
                    if (!list) return;
                    fetch('/admin/api/gallery/pending').then(r => r.json()).then(data => {{
                        if (!data.items.length) {{ list.innerHTML = '<p>Нет фото на модерации.</p>'; return; }}
                        list.innerHTML = data.items.map(item => `
                            <div class="block-item">
                                <div class="block-header"><span class="block-ip">ID: ${{item.id}}</span><button class="btn btn-success" onclick="approvePhoto(${{item.id}})">Одобрить</button></div>
                                <div class="block-reason">Отправил: ${{item.submitted_by}}</div>
                                <img class="preview-image" src="${{item.image_url}}" alt="preview">
                                <div><a href="${{item.image_url}}" target="_blank" style="color:#ff9900">${{item.image_url}}</a></div>
                            </div>
                        `).join('');
                    }});
                }}

                function approvePhoto(submissionId) {{
                    fetch('/admin/api/gallery/approve', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ submission_id: submissionId }}) }})
                    .then(r => r.json()).then(data => {{
                        alert(data.message);
                        if (data.success) loadPendingGallery();
                    }});
                }}

                document.getElementById('blockIpForm').addEventListener('submit', function(e) {{
                    e.preventDefault();
                    const formData = {{ ip_address: ip_address.value, block_reason: block_reason.value, expires_hours: expires_hours.value }};
                    fetch('/admin/api/manual-blocks/add', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(formData) }})
                    .then(r => r.json()).then(data => {{
                        if (data.success) {{ this.reset(); loadManualBlocks(); }} else alert(data.message || 'Ошибка блокировки');
                    }});
                }});

                document.getElementById('addImageForm').addEventListener('submit', function(e) {{
                    e.preventDefault();
                    fetch('/admin/api/gallery/add', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ image_url: image_url.value }}) }})
                    .then(r => r.json()).then(data => {{
                        alert(data.message);
                        if (data.success) this.reset();
                    }});
                }});

                const addAdminForm = document.getElementById('addAdminForm');
                if (addAdminForm) {{
                    addAdminForm.addEventListener('submit', function(e) {{
                        e.preventDefault();
                        fetch('/admin/api/admins/add', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ username: new_admin_username.value, password: new_admin_password.value }}) }})
                        .then(r => r.json()).then(data => alert(data.message));
                    }});
                }}

                setInterval(updateStats, 5000);
                updateStats();
            </script>
        </body>
        </html>
        """

    def get_admin_login_page_content(self):
        """Генерация HTML контента для страницы логина админки"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Вход в панель управления - Клан BENZ</title>
            <meta charset="utf-8">
            <style>
                body { 
                    font-family: Arial, sans-serif; 
                    background: linear-gradient(135deg, #1a1a1a, #2d2d2d);
                    margin: 0; 
                    padding: 0; 
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    color: white;
                }
                .login-container {
                    background: #2a2a2a;
                    padding: 40px;
                    border-radius: 10px;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                    width: 100%;
                    max-width: 400px;
                    border: 1px solid #444;
                }
                .login-header {
                    text-align: center;
                    margin-bottom: 30px;
                }
                .login-header h1 {
                    color: #ff9900;
                    margin-bottom: 10px;
                }
                .form-group {
                    margin-bottom: 20px;
                }
                .form-group label {
                    display: block;
                    margin-bottom: 5px;
                    color: #cccccc;
                }
                .form-group input {
                    width: 100%;
                    padding: 12px;
                    background: #1a1a1a;
                    border: 1px solid #444;
                    border-radius: 5px;
                    color: white;
                    font-size: 16px;
                    box-sizing: border-box;
                }
                .form-group input:focus {
                    outline: none;
                    border-color: #ff9900;
                }
                .btn-login {
                    width: 100%;
                    padding: 12px;
                    background: #ff9900;
                    color: black;
                    border: none;
                    border-radius: 5px;
                    font-size: 16px;
                    font-weight: bold;
                    cursor: pointer;
                    transition: background 0.3s;
                }
                .btn-login:hover {
                    background: #e68a00;
                }
                .error-message {
                    color: #ff4444;
                    text-align: center;
                    margin-top: 15px;
                    display: none;
                }
                .back-btn {
                    background: #666;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    font-weight: bold;
                    display: inline-block;
                    margin-top: 15px;
                    text-align: center;
                    width: 100%;
                    box-sizing: border-box;
                }
                .back-btn:hover {
                    background: #777;
                }
            </style>
        </head>
        <body>
            <div class="login-container">
                <div class="login-header">
                    <h1>Клан BENZ</h1>
                    <p>Панель управления</p>
                </div>
                <form id="loginForm">
                    <div class="form-group">
                        <label for="username">Логин:</label>
                        <input type="text" id="username" name="username" value="main_admin" required>
                    </div>
                    <div class="form-group">
                        <label for="password">Пароль:</label>
                        <input type="password" id="password" name="password" required>
                    </div>
                    <button type="submit" class="btn-login">Войти</button>
                </form>
                <a href="/" class="back-btn">← Вернуться на сайт</a>
                <div class="error-message" id="errorMessage">
                    Неверный пароль!
                </div>
            </div>

            <script>
                document.getElementById('loginForm').addEventListener('submit', async function(e) {
                    e.preventDefault();
                    const username = document.getElementById('username').value;
                    const password = document.getElementById('password').value;

                    const response = await fetch('/admin/api/login', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ username: username, password: password })
                    });

                    if (response.ok) {
                        window.location.href = '/admin';
                    } else {
                        document.getElementById('errorMessage').style.display = 'block';
                    }
                });
            </script>
        </body>
        </html>
        """

    def log_message(self, format, *args):
        logger.info("%s - %s" % (self.client_address[0], format % args))


# ==================== ЗАПУСК СЕРВИСОВ ====================

def run_server():
    """Запуск веб-сервера"""
    global server_httpd
    try:
        server_address = ('', SERVER_PORT)
        server_httpd = ThreadingHTTPServer(server_address, ClanRequestHandler)
        logger.info(f"Сервер запущен на порту {SERVER_PORT}")
        logger.info(f"Админка доступна по адресу: http://localhost:{SERVER_PORT}/admin")
        logger.info(f"Главный админ: {MAIN_ADMIN_USERNAME}")
        logger.info(f"Пароль главного админа: {MAIN_ADMIN_PASSWORD}")
        logger.info(f"Режим обслуживания: {'ВКЛЮЧЕН' if MAINTENANCE_MODE else 'ВЫКЛЮЧЕН'}")
        server_httpd.serve_forever()
    except Exception as e:
        logger.error(f"Ошибка запуска сервера: {e}")


# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================

def main():
    """Главная функция"""
    print("Запуск системы управления кланом BENZ...")
    print("=" * 50)

    # Инициализация баз данных
    init_databases()

    # Загрузка режима обслуживания
    load_maintenance_mode()

    print("Сервер клана запущен")
    print(f"Админка доступна по адресу: http://localhost:{SERVER_PORT}/admin")
    print(f"Главный админ: {MAIN_ADMIN_USERNAME}")
    print(f"Пароль главного админа: {MAIN_ADMIN_PASSWORD}")
    print(f"Режим обслуживания: {'ВКЛЮЧЕН' if MAINTENANCE_MODE else 'ВЫКЛЮЧЕН'}")
    print("\nДля остановки нажмите Ctrl+C")
    print("=" * 50)

    # Запуск сервера (блокирующий вызов)
    run_server()


if __name__ == '__main__':
    main()
