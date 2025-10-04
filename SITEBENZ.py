import logging
import threading
import sqlite3
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timedelta
import secrets
import os
import time

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
MANAGE_PASSWORD = "admin123"

# Ограничения посещений
VISIT_LIMIT = 15  # Максимум 15 посещений в минуту
VISIT_BLOCK_TIME = 60  # Блокировка на 1 минуту при превышении

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

# Защита от DDoS атак
ddos_protection_db = "ddos_protection.db"
REQUEST_LIMIT = 100  # 100 запросов в минуту для DDoS защиты
BLOCK_TIME = 300  # Блокировка на 5 минут для DDoS
ip_request_times = {}

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
        conn.commit()
        conn.close()
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
    """Проверка ограничения посещений (15 в минуту)"""
    try:
        # Сначала проверяем ручную блокировку
        is_manual_blocked, block_info = is_ip_manually_blocked(ip_address)
        if is_manual_blocked:
            logger.warning(f"Доступ запрещен: IP {ip_address} заблокирован вручную. Причина: {block_info['reason']}")
            return False, "manual_block"

        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()

        current_time = datetime.now()
        one_minute_ago = (current_time - timedelta(minutes=1)).isoformat()

        # Проверяем, заблокирован ли IP за превышение лимита посещений
        cursor.execute('''
            SELECT block_start_time, is_blocked, block_reason FROM ip_blocks 
            WHERE ip_address = ? AND is_blocked = TRUE AND is_manual_block = FALSE
        ''', (ip_address,))

        blocked_ip = cursor.fetchone()

        if blocked_ip:
            block_start_time = datetime.fromisoformat(blocked_ip[0])
            block_reason = blocked_ip[2] if blocked_ip[2] else 'ddos'
            time_diff = current_time - block_start_time

            # Если прошло больше времени блокировки - разблокируем
            if time_diff.total_seconds() >= VISIT_BLOCK_TIME:
                cursor.execute('''
                    UPDATE ip_blocks 
                    SET is_blocked = FALSE, request_count = 1 
                    WHERE ip_address = ? AND is_manual_block = FALSE
                ''', (ip_address,))
                conn.commit()
                logger.info(f"IP разблокирован после превышения лимита: {ip_address}")
            else:
                conn.close()
                # Если заблокирован за превышение лимита посещений
                if block_reason == 'visit_limit':
                    return False, "visit_limit"
                # Если заблокирован за DDoS
                else:
                    return False, "ddos"

        # Подсчитываем все запросы за последнюю минуту
        cursor.execute('''
            SELECT COUNT(*) FROM request_logs 
            WHERE ip_address = ? AND timestamp > ?
        ''', (ip_address, one_minute_ago))

        total_requests = cursor.fetchone()[0]

        # Если превышен лимит посещений - блокируем IP
        if total_requests >= VISIT_LIMIT:
            cursor.execute('''
                INSERT OR REPLACE INTO ip_blocks 
                (ip_address, block_start_time, is_blocked, request_count, block_reason, is_manual_block)
                VALUES (?, ?, TRUE, ?, 'visit_limit', FALSE)
            ''', (ip_address, current_time.isoformat(), total_requests))
            conn.commit()
            conn.close()
            logger.warning(f"IP заблокирован за превышение лимита посещений: {ip_address}, запросов: {total_requests}")
            return False, "visit_limit"

        # Логируем текущий запрос
        cursor.execute('''
            INSERT INTO request_logs (ip_address, path, timestamp)
            VALUES (?, ?, ?)
        ''', (ip_address, path, current_time.isoformat()))

        # Обновляем счетчик в ip_blocks
        cursor.execute('''
            INSERT OR REPLACE INTO ip_blocks 
            (ip_address, block_start_time, request_count, is_blocked, block_reason, is_manual_block)
            VALUES (?, ?, ?, FALSE, 'normal', FALSE)
        ''', (ip_address, current_time.isoformat(), total_requests + 1))

        conn.commit()
        conn.close()

        logger.debug(f"Запрос от {ip_address} ({path}), всего запросов за минуту: {total_requests + 1}")
        return True, "allowed"

    except Exception as e:
        logger.error(f"Ошибка проверки ограничения посещений: {e}")
        return True, "error"


def check_ddos_protection(ip_address):
    """Проверка защиты от DDoS атак (более строгие лимиты)"""
    try:
        conn = sqlite3.connect(ddos_protection_db)
        cursor = conn.cursor()

        current_time = datetime.now()
        one_minute_ago = (current_time - timedelta(minutes=1)).isoformat()

        # Получаем количество запросов за последнюю минуту
        cursor.execute('''
            SELECT COUNT(*) FROM request_logs 
            WHERE ip_address = ? AND timestamp > ?
        ''', (ip_address, one_minute_ago))

        request_count = cursor.fetchone()[0]

        # Если превышен DDoS лимит - блокируем IP
        if request_count >= REQUEST_LIMIT:
            cursor.execute('''
                INSERT OR REPLACE INTO ip_blocks 
                (ip_address, block_start_time, is_blocked, request_count, block_reason, is_manual_block)
                VALUES (?, ?, TRUE, ?, 'ddos', FALSE)
            ''', (ip_address, current_time.isoformat(), request_count))
            conn.commit()
            conn.close()
            logger.warning(f"IP заблокирован за DDoS: {ip_address}, запросов: {request_count}")
            return False

        conn.close()
        return True

    except Exception as e:
        logger.error(f"Ошибка проверки DDoS защиты: {e}")
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


def check_admin_auth(cookie_header):
    """Проверка авторизации администратора"""
    if not cookie_header:
        return False

    try:
        cookies = parse_cookies(cookie_header)
        session_id = cookies.get('admin_session')
        if session_id and session_id in admin_sessions:
            # Проверяем время жизни сессии (1 час)
            session_time = admin_sessions[session_id]
            if (datetime.now() - session_time).total_seconds() < 3600:
                # Обновляем время сессии
                admin_sessions[session_id] = datetime.now()
                return True
            else:
                # Удаляем просроченную сессию
                del admin_sessions[session_id]
    except:
        pass
    return False


def parse_cookies(cookie_header):
    """Парсинг cookies"""
    cookies = {}
    for cookie in cookie_header.split(';'):
        if '=' in cookie:
            key, value = cookie.strip().split('=', 1)
            cookies[key] = value
    return cookies


def create_admin_session():
    """Создание новой сессии администратора"""
    session_id = secrets.token_hex(16)
    admin_sessions[session_id] = datetime.now()
    return session_id


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

    def _check_protection(self):
        """Проверка защиты от DDoS и ограничения посещений"""
        ip_address = self.client_address[0]

        # Очищаем старые логи раз в 20 запросов (для оптимизации)
        if hash(ip_address) % 20 == 0:
            cleanup_old_logs()

        # Сначала проверяем ограничение посещений (15 в минуту)
        visit_allowed, visit_reason = check_visit_limit(ip_address, self.path)

        if not visit_allowed:
            if visit_reason == "manual_block":
                self._send_manual_block_error(ip_address)
                return False
            elif visit_reason == "visit_limit":
                self._send_visit_limit_error(ip_address)
                return False
            else:
                # Если заблокирован за DDoS
                self._send_ddos_error(ip_address)
                return False

        # Затем проверяем защиту от DDoS (более строгие лимиты)
        if not check_ddos_protection(ip_address):
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
        """Отправка ошибки превышения лимита посещений"""
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
                    <p><strong>Ограничение:</strong> не более {VISIT_LIMIT} посещений в минуту</p>
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
        if path == '/':
            self.serve_html()
        elif path == '/zayavka':
            self.serve_application_page()
        elif path == '/applications':
            self.serve_applications()
        elif path == '/statistics':
            self.serve_statistics()
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
        return """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Клан BENZ - Rust</title>
            <style>
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                }

                body {
                    background-color: #1a1a1a;
                    color: #e0e0e0;
                    line-height: 1.6;
                }

                header {
                    background: linear-gradient(to right, #222, #333);
                    padding: 1rem 0;
                    text-align: center;
                    border-bottom: 3px solid #ff9900;
                    position: relative;
                }

                .header-top {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 0 2rem;
                    margin-bottom: 1rem;
                }

                .header-buttons {
                    display: flex;
                    gap: 1rem;
                }

                .header-btn {
                    background: #ff9900;
                    color: #1a1a1a;
                    border: none;
                    padding: 0.6rem 1.2rem;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    transition: background 0.3s;
                    text-decoration: none;
                    font-size: 0.9rem;
                }

                .header-btn:hover {
                    background: #e68a00;
                }

                .header-btn.admin {
                    background: #666;
                    color: white;
                }

                .header-btn.admin:hover {
                    background: #777;
                }

                .container {
                    max-width: 1200px;
                    margin: 0 auto;
                    padding: 0 20px;
                }

                h1 {
                    font-size: 3rem;
                    color: #ff9900;
                    text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.5);
                    margin-bottom: 0.5rem;
                }

                .tagline {
                    font-size: 1.2rem;
                    color: #cccccc;
                    font-style: italic;
                }

                .hero {
                    background: linear-gradient(135deg, #2a2a2a 0%, #1a1a1a 100%);
                    padding: 4rem 0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    text-align: center;
                    position: relative;
                    border-bottom: 1px solid #444;
                }

                .hero-content {
                    position: relative;
                    z-index: 1;
                }

                .hero h2 {
                    font-size: 2.5rem;
                    margin-bottom: 1rem;
                    color: #ff9900;
                }

                .hero p {
                    font-size: 1.2rem;
                    max-width: 700px;
                    margin: 0 auto;
                    color: #cccccc;
                }

                .section {
                    padding: 4rem 0;
                }

                .section-title {
                    text-align: center;
                    margin-bottom: 2rem;
                    color: #ff9900;
                    font-size: 2rem;
                }

                .features {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                    gap: 2rem;
                    margin-bottom: 3rem;
                }

                .feature {
                    background: #2a2a2a;
                    padding: 2rem;
                    border-radius: 8px;
                    text-align: center;
                    transition: transform 0.3s;
                    border: 1px solid #444;
                }

                .feature:hover {
                    transform: translateY(-5px);
                    border-color: #ff9900;
                }

                .feature-icon {
                    font-size: 3rem;
                    margin-bottom: 1rem;
                    color: #ff9900;
                }

                .feature h3 {
                    margin-bottom: 1rem;
                    color: #ff9900;
                }

                /* Галерея */
                .gallery-section {
                    background: #1a1a1a;
                    padding: 3rem 0;
                }

                .gallery-container {
                    max-width: 1400px;
                    margin: 0 auto;
                    padding: 0 20px;
                }

                .gallery {
                    display: flex;
                    overflow-x: auto;
                    scroll-behavior: smooth;
                    gap: 25px;
                    padding: 30px 0;
                    scrollbar-width: thin;
                    scrollbar-color: #ff9900 #2a2a2a;
                }

                .gallery::-webkit-scrollbar {
                    height: 12px;
                }

                .gallery::-webkit-scrollbar-track {
                    background: #2a2a2a;
                    border-radius: 10px;
                }

                .gallery::-webkit-scrollbar-thumb {
                    background: #ff9900;
                    border-radius: 10px;
                }

                .gallery-item {
                    flex: 0 0 auto;
                    width: 500px;
                    height: 350px;
                    border-radius: 15px;
                    overflow: hidden;
                    position: relative;
                    transition: transform 0.3s ease, box-shadow 0.3s ease;
                    border: 3px solid transparent;
                    cursor: pointer;
                }

                .gallery-item:hover {
                    transform: scale(1.05);
                    box-shadow: 0 15px 35px rgba(255, 153, 0, 0.4);
                    border-color: #ff9900;
                }

                .gallery-item img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                    transition: transform 0.3s ease;
                }

                .gallery-item:hover img {
                    transform: scale(1.1);
                }

                .gallery-nav {
                    display: flex;
                    justify-content: center;
                    gap: 15px;
                    margin-top: 30px;
                }

                .gallery-nav-btn {
                    background: #ff9900;
                    color: #1a1a1a;
                    border: none;
                    padding: 12px 25px;
                    border-radius: 8px;
                    cursor: pointer;
                    font-weight: bold;
                    transition: background 0.3s;
                    font-size: 16px;
                }

                .gallery-nav-btn:hover {
                    background: #e68a00;
                }

                /* Модальное окно */
                .modal {
                    display: none;
                    position: fixed;
                    z-index: 1000;
                    left: 0;
                    top: 0;
                    width: 100%;
                    height: 100%;
                    background-color: rgba(0,0,0,0.9);
                    animation: fadeIn 0.3s;
                }

                @keyframes fadeIn {
                    from { opacity: 0; }
                    to { opacity: 1; }
                }

                .modal-content {
                    margin: auto;
                    display: block;
                    max-width: 95%;
                    max-height: 95%;
                    margin-top: 2%;
                    border-radius: 10px;
                    box-shadow: 0 0 50px rgba(255, 153, 0, 0.3);
                }

                .close {
                    position: absolute;
                    top: 20px;
                    right: 35px;
                    color: #ff9900;
                    font-size: 50px;
                    font-weight: bold;
                    cursor: pointer;
                    transition: color 0.3s;
                }

                .close:hover {
                    color: #fff;
                }

                .modal-caption {
                    text-align: center;
                    color: #fff;
                    padding: 15px;
                    font-size: 18px;
                }

                /* Кнопка заявки */
                .application-section {
                    text-align: center;
                    padding: 3rem 0;
                    background: #2a2a2a;
                }

                .application-btn {
                    background: #ff9900;
                    color: #1a1a1a;
                    border: none;
                    padding: 1.2rem 3rem;
                    font-size: 1.3rem;
                    border-radius: 8px;
                    cursor: pointer;
                    font-weight: bold;
                    transition: all 0.3s;
                    text-decoration: none;
                    display: inline-block;
                    margin: 1rem 0;
                }

                .application-btn:hover {
                    background: #e68a00;
                    transform: translateY(-2px);
                    box-shadow: 0 10px 20px rgba(255, 153, 0, 0.3);
                }

                .server-info {
                    background: #2a2a2a;
                    padding: 1rem;
                    border-radius: 8px;
                    text-align: center;
                    margin: 1rem auto;
                    max-width: 600px;
                    border: 1px solid #444;
                }

                footer {
                    background: #222;
                    padding: 2rem 0;
                    text-align: center;
                    border-top: 1px solid #444;
                    margin-top: 2rem;
                }

                @media (max-width: 768px) {
                    .container {
                        padding: 0 15px;
                    }

                    .header-top {
                        flex-direction: column;
                        gap: 1rem;
                        padding: 0 1rem;
                    }

                    .header-buttons {
                        justify-content: center;
                    }

                    h1 {
                        font-size: 2.2rem;
                    }

                    .hero h2 {
                        font-size: 1.8rem;
                    }

                    .section {
                        padding: 2rem 0;
                    }

                    .features {
                        grid-template-columns: 1fr;
                    }

                    .application-btn {
                        padding: 1rem 2rem;
                        font-size: 1.1rem;
                    }

                    .gallery-item {
                        width: 380px;
                        height: 280px;
                    }

                    .modal-content {
                        max-width: 98%;
                        max-height: 85%;
                        margin-top: 5%;
                    }

                    .close {
                        top: 10px;
                        right: 20px;
                        font-size: 40px;
                    }
                }
            </style>
        </head>
        <body>
            <header>
                <div class="header-top">
                    <div class="header-buttons">
                        <a href="/zayavka" class="header-btn">Подать заявку</a>
                        <a href="/admin" class="header-btn admin">Войти в админку</a>
                    </div>
                </div>
                <div class="container">
                    <h1>КЛАН BENZ</h1>
                    <p class="tagline">Самый крутой клан в Rust</p>
                </div>
            </header>

            <!-- Галерея -->
            <section class="gallery-section">
                <div class="gallery-container">
                    <h2 class="section-title">Галерея клана</h2>
                    <div class="gallery" id="gallery">
                        <!-- Изображения будут загружены через JavaScript -->
                    </div>
                    <div class="gallery-nav">
                        <button class="gallery-nav-btn" onclick="scrollGallery(-400)">← Назад</button>
                        <button class="gallery-nav-btn" onclick="scrollGallery(400)">Вперед →</button>
                    </div>
                </div>
            </section>

            <!-- Модальное окно для полноэкранного просмотра -->
            <div id="imageModal" class="modal">
                <span class="close" onclick="closeModal()">&times;</span>
                <img class="modal-content" id="modalImage">
                <div class="modal-caption" id="modalCaption"></div>
            </div>

            <!-- Секция "Почему BENZ?" -->
            <section class="section">
                <div class="container">
                    <h2 class="section-title">Почему BENZ?</h2>
                    <div class="features">
                        <div class="feature">
                            <div class="feature-icon">⚔️</div>
                            <h3>Сильная команда</h3>
                            <p>Опытные игроки с тысячами часов в игре, готовые прийти на помощь в любой ситуации.</p>
                        </div>
                        <div class="feature">
                            <div class="feature-icon">🏰</div>
                            <h3>Неприступные базы</h3>
                            <p>Строим крепости, которые выдерживают самые серьезные рейды и осады.</p>
                        </div>
                        <div class="feature">
                            <div class="feature-icon">💎</div>
                            <h3>Богатые ресурсы</h3>
                            <p>Постоянный доступ к лучшему оружию, броне и транспортным средствам.</p>
                        </div>
                    </div>
                </div>
            </section>

            <!-- Секция с кнопкой заявки -->
            <section class="application-section">
                <div class="container">
                    <h2 class="section-title">Готов вступить в клан?</h2>
                    <p style="margin-bottom: 2rem; font-size: 1.2rem;">Нажмите на кнопку ниже, чтобы подать заявку на вступление</p>
                    <a href="/zayavka" class="application-btn">Подать заявку</a>

                    <div class="server-info">
                        <strong>Статус:</strong> Сервер работает | <strong>Требования:</strong> 1500+ часов в игре
                    </div>
                </div>
            </section>

            <footer>
                <div class="container">
                    <p><strong>Клан BENZ</strong> © 2024 | Rust</p>
                    <p>Присоединяйся к нам и стань частью легенды!</p>
                </div>
            </footer>

            <script>
                const GALLERY_IMAGES = """ + json.dumps(GALLERY_IMAGES) + """;

                // Загрузка галереи
                function loadGallery() {
                    try {
                        const gallery = document.getElementById('gallery');
                        gallery.innerHTML = '';

                        GALLERY_IMAGES.forEach((imageUrl, index) => {
                            const galleryItem = document.createElement('div');
                            galleryItem.className = 'gallery-item';
                            galleryItem.innerHTML = `
                                <img src="${imageUrl}" alt="Фото клана BENZ ${index + 1}" loading="lazy">
                            `;
                            galleryItem.onclick = function() {
                                openModal(imageUrl, index + 1);
                            };
                            gallery.appendChild(galleryItem);
                        });
                    } catch (error) {
                        console.error('Ошибка загрузки галереи:', error);
                    }
                }

                // Плавная прокрутка галереи
                function scrollGallery(distance) {
                    const gallery = document.getElementById('gallery');
                    gallery.scrollBy({
                        left: distance,
                        behavior: 'smooth'
                    });
                }

                // Открытие модального окна
                function openModal(imageUrl, imageNumber) {
                    const modal = document.getElementById('imageModal');
                    const modalImg = document.getElementById('modalImage');
                    const caption = document.getElementById('modalCaption');

                    modal.style.display = 'block';
                    modalImg.src = imageUrl;
                    caption.textContent = `Фото клана BENZ (${imageNumber}/${GALLERY_IMAGES.length})`;

                    // Блокировка прокрутки body
                    document.body.style.overflow = 'hidden';
                }

                // Закрытие модального окна
                function closeModal() {
                    const modal = document.getElementById('imageModal');
                    modal.style.display = 'none';
                    document.body.style.overflow = 'auto';
                }

                // Закрытие модального окна при клике вне изображения
                window.onclick = function(event) {
                    const modal = document.getElementById('imageModal');
                    if (event.target === modal) {
                        closeModal();
                    }
                }

                // Закрытие модального окна клавишей ESC
                document.addEventListener('keydown', function(event) {
                    if (event.key === 'Escape') {
                        closeModal();
                    }
                });

                // Загружаем галерею при загрузке страницы
                document.addEventListener('DOMContentLoaded', loadGallery);
            </script>
        </body>
        </html>
        """

    def get_application_page_content(self, can_submit):
        """Генерация HTML контента для страницы заявки"""
        if not can_submit:
            return """
            <!DOCTYPE html>
            <html lang="ru">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Заявка - Клан BENZ</title>
                <style>
                    body {
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background: #1a1a1a;
                        color: #e0e0e0;
                        margin: 0;
                        padding: 0;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        min-height: 100vh;
                    }
                    .container {
                        background: #2a2a2a;
                        padding: 3rem;
                        border-radius: 10px;
                        text-align: center;
                        border: 2px solid #ff9900;
                        max-width: 500px;
                        margin: 2rem;
                    }
                    h1 {
                        color: #ff9900;
                        margin-bottom: 1rem;
                    }
                    .message {
                        font-size: 1.2rem;
                        margin-bottom: 2rem;
                        line-height: 1.6;
                    }
                    .btn {
                        background: #666;
                        color: white;
                        padding: 1rem 2rem;
                        text-decoration: none;
                        border-radius: 5px;
                        font-weight: bold;
                        display: inline-block;
                    }
                    .btn:hover {
                        background: #777;
                    }
                    .icon {
                        font-size: 4rem;
                        margin-bottom: 1rem;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="icon">⏰</div>
                    <h1>Заявка уже отправлена</h1>
                    <div class="message">
                        Вы уже отправили заявку на вступление в клан.<br>
                        Пожалуйста, подождите 1 час перед отправкой следующей заявки.
                    </div>
                    <a href="/" class="btn">Вернуться на главную</a>
                </div>
            </body>
            </html>
            """

        return """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Подать заявку - Клан BENZ</title>
            <style>
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                }

                body {
                    background-color: #1a1a1a;
                    color: #e0e0e0;
                    line-height: 1.6;
                }

                header {
                    background: linear-gradient(to right, #222, #333);
                    padding: 1rem 0;
                    text-align: center;
                    border-bottom: 3px solid #ff9900;
                    position: relative;
                }

                .header-top {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 0 2rem;
                    margin-bottom: 1rem;
                }

                .header-buttons {
                    display: flex;
                    gap: 1rem;
                }

                .header-btn {
                    background: #ff9900;
                    color: #1a1a1a;
                    border: none;
                    padding: 0.6rem 1.2rem;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    transition: background 0.3s;
                    text-decoration: none;
                    font-size: 0.9rem;
                }

                .header-btn:hover {
                    background: #e68a00;
                }

                .header-btn.admin {
                    background: #666;
                    color: white;
                }

                .header-btn.admin:hover {
                    background: #777;
                }

                .container {
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 0 20px;
                }

                h1 {
                    font-size: 2.5rem;
                    color: #ff9900;
                    text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.5);
                    margin-bottom: 0.5rem;
                }

                .back-btn {
                    background: #666;
                    color: white;
                    padding: 0.8rem 1.5rem;
                    text-decoration: none;
                    border-radius: 5px;
                    font-weight: bold;
                    display: inline-block;
                    margin-top: 1rem;
                }

                .back-btn:hover {
                    background: #777;
                }

                .section {
                    padding: 3rem 0;
                }

                .application-form {
                    background: #2a2a2a;
                    padding: 2rem;
                    border-radius: 8px;
                    border: 1px solid #444;
                }

                .form-group {
                    margin-bottom: 1.5rem;
                }

                label {
                    display: block;
                    margin-bottom: 0.5rem;
                    color: #ff9900;
                    font-weight: bold;
                }

                input, textarea, select {
                    width: 100%;
                    padding: 0.8rem;
                    background: #1a1a1a;
                    border: 1px solid #444;
                    border-radius: 4px;
                    color: #e0e0e0;
                    font-size: 1rem;
                }

                input:focus, textarea:focus, select:focus {
                    outline: none;
                    border-color: #ff9900;
                }

                button {
                    background: #ff9900;
                    color: #1a1a1a;
                    border: none;
                    padding: 1rem 2rem;
                    font-size: 1.1rem;
                    border-radius: 4px;
                    cursor: pointer;
                    font-weight: bold;
                    transition: background 0.3s;
                    width: 100%;
                }

                button:hover {
                    background: #e68a00;
                }

                .success-message {
                    background: #2a2a2a;
                    padding: 2rem;
                    border-radius: 8px;
                    text-align: center;
                    border: 2px solid #4CAF50;
                    display: none;
                }

                .error-message {
                    background: #2a2a2a;
                    padding: 1rem;
                    border-radius: 8px;
                    text-align: center;
                    border: 2px solid #ff4444;
                    color: #ff4444;
                    display: none;
                    margin-bottom: 1rem;
                }

                .server-info {
                    background: #2a2a2a;
                    padding: 1rem;
                    border-radius: 8px;
                    text-align: center;
                    margin: 1rem auto;
                    border: 1px solid #444;
                }

                @media (max-width: 768px) {
                    .container {
                        padding: 0 15px;
                    }

                    .header-top {
                        flex-direction: column;
                        gap: 1rem;
                        padding: 0 1rem;
                    }

                    .header-buttons {
                        justify-content: center;
                    }

                    h1 {
                        font-size: 2rem;
                    }

                    .application-form {
                        padding: 1.5rem;
                    }
                }
            </style>
        </head>
        <body>
            <header>
                <div class="header-top">
                    <div class="header-buttons">
                        <a href="/zayavka" class="header-btn">Подать заявку</a>
                        <a href="/admin" class="header-btn admin">Войти в админку</a>
                    </div>
                </div>
                <div class="container">
                    <h1>Подать заявку в клан BENZ</h1>
                    <a href="/" class="back-btn">← Назад на главную</a>
                </div>
            </header>

            <section class="section">
                <div class="container">
                    <div class="server-info">
                        <strong>Требования:</strong> 1500+ часов в игре | <strong>Ограничение:</strong> 1 заявка в час
                    </div>

                    <div class="error-message" id="errorMessage">
                        Произошла ошибка при отправке заявки. Пожалуйста, попробуйте еще раз.
                    </div>

                    <div class="application-form" id="applicationForm">
                        <form id="clanApplication">
                            <div class="form-group">
                                <label for="nickname">Игровой никнейм *</label>
                                <input type="text" id="nickname" name="nickname" required placeholder="Введите ваш никнейм в игре">
                            </div>

                            <div class="form-group">
                                <label for="steamId">Steam ID или профиль *</label>
                                <input type="text" id="steamId" name="steamId" required placeholder="Например: STEAM_0:1:12345678 или ссылка на профиль">
                            </div>

                            <div class="form-group">
                                <label for="playtime">Часов в игре * (минимум 1500 часов)</label>
                                <input type="number" id="playtime" name="playtime" required placeholder="Количество часов в Rust" min="1500">
                            </div>

                            <div class="form-group">
                                <label for="discord">Discord username *</label>
                                <input type="text" id="discord" name="discord" required placeholder="Например: username#1234">
                            </div>

                            <div class="form-group">
                                <label for="role">Предпочитаемая роль в клане *</label>
                                <select id="role" name="role" required>
                                    <option value="">Выберите роль</option>
                                    <option value="Фермер">Фермер ресурсов</option>
                                    <option value="Строитель">Строитель баз</option>
                                    <option value="Боец">Комбатер</option>
                                    <option value="Коллер">Коллер</option>
                                    <option value="Универсал">Универсал</option>
                                </select>
                            </div>

                            <div class="form-group">
                                <label for="message">Почему вы хотите вступить в наш клан? *</label>
                                <textarea id="message" name="message" rows="4" required placeholder="Расскажите о себе, вашем опыте и почему мы должны принять вас в клан..."></textarea>
                            </div>

                            <button type="submit" id="submitBtn">Отправить заявку</button>
                        </form>
                    </div>

                    <div class="success-message" id="successMessage">
                        <h3>Заявка отправлена!</h3>
                        <p>Спасибо за вашу заявку в клан BENZ! Мы рассмотрим её в ближайшее время и свяжемся с вами через Discord.</p>
                        <a href="/" class="back-btn" style="margin-top: 1rem;">Вернуться на главную</a>
                    </div>
                </div>
            </section>

            <script>
                // Обработчик формы заявки
                document.getElementById('clanApplication').addEventListener('submit', async function(e) {
                    e.preventDefault();

                    const submitBtn = document.getElementById('submitBtn');
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Отправка...';
                    document.getElementById('errorMessage').style.display = 'none';

                    const formData = {
                        nickname: document.getElementById('nickname').value,
                        steamId: document.getElementById('steamId').value,
                        playtime: document.getElementById('playtime').value,
                        discord: document.getElementById('discord').value,
                        role: document.getElementById('role').value,
                        message: document.getElementById('message').value
                    };

                    // Проверка минимального количества часов
                    if (parseInt(formData.playtime) < 1500) {
                        document.getElementById('errorMessage').textContent = 'Минимальное количество часов для подачи заявки - 1500!';
                        document.getElementById('errorMessage').style.display = 'block';
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Отправить заявку';
                        return;
                    }

                    try {
                        const response = await fetch('/submit_application', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                            },
                            body: new URLSearchParams(formData)
                        });

                        if (!response.ok) {
                            const errorData = await response.json();
                            throw new Error(errorData.message || `HTTP error! status: ${response.status}`);
                        }

                        const result = await response.json();

                        if (result.status === 'success') {
                            document.getElementById('applicationForm').style.display = 'none';
                            document.getElementById('successMessage').style.display = 'block';
                        } else {
                            throw new Error(result.message || 'Ошибка сервера');
                        }

                    } catch (error) {
                        console.error('Ошибка:', error);
                        document.getElementById('errorMessage').textContent = error.message || 'Произошла неизвестная ошибка';
                        document.getElementById('errorMessage').style.display = 'block';
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Отправить заявку';
                    }
                });
            </script>
        </body>
        </html>
        """

    def handle_application(self):
        """Обработка заявки"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self._set_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': 'Пустые данные'}).encode())
                return

            post_data = self.rfile.read(content_length)
            form_data = parse_qs(post_data.decode('utf-8'))

            # Валидация обязательных полей
            required_fields = ['nickname', 'steamId', 'playtime', 'discord', 'role', 'message']
            for field in required_fields:
                if field not in form_data or not form_data[field][0].strip():
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self._set_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'error', 'message': f'Поле {field} обязательно'}).encode())
                    return

            application_data = {
                'nickname': form_data['nickname'][0].strip(),
                'steamId': form_data['steamId'][0].strip(),
                'playtime': form_data['playtime'][0].strip(),
                'discord': form_data['discord'][0].strip(),
                'role': form_data['role'][0].strip(),
                'message': form_data['message'][0].strip(),
                'ip': self.client_address[0]
            }

            # Проверка часов
            try:
                playtime = int(application_data['playtime'])
                if playtime < 1500:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self._set_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'error', 'message': 'Минимум 1500 часов!'}).encode())
                    return
            except ValueError:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self._set_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': 'Некорректное количество часов'}).encode())
                return

            # Проверка лимита заявок
            if not can_submit_application(application_data['ip']):
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self._set_cors_headers()
                self.end_headers()
                self.wfile.write(
                    json.dumps({'status': 'error', 'message': 'Вы уже отправили заявку. Подождите 1 час.'}).encode())
                return

            # Сохранение заявки
            application_id = save_application(application_data)

            if application_id is None:
                raise Exception("Не удалось сохранить заявку в базу данных")

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(
                json.dumps({'status': 'success', 'message': 'Заявка отправлена!', 'id': application_id}).encode())

            logger.info(f"Новая заявка #{application_id} от {application_data['nickname']}")

        except Exception as e:
            logger.error(f"Ошибка обработки заявки: {e}", exc_info=True)
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'error', 'message': 'Внутренняя ошибка сервера'}).encode())

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

    def serve_statistics(self):
        """API для получения статистики"""
        try:
            stats = get_statistics()
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
            self.wfile.write(json.dumps(GALLERY_IMAGES).encode('utf-8'))
        except Exception as e:
            logger.error(f"Error serving gallery images: {e}")
            self.send_error(500)

    def serve_rate_limit_status(self):
        """API для проверки текущего статуса ограничений"""
        try:
            ip_address = self.client_address[0]
            conn = sqlite3.connect(ddos_protection_db)
            cursor = conn.cursor()

            current_time = datetime.now()
            one_minute_ago = (current_time - timedelta(minutes=1)).isoformat()

            # Получаем количество запросов за последнюю минуту
            cursor.execute('''
                SELECT COUNT(*) FROM request_logs 
                WHERE ip_address = ? AND timestamp > ?
            ''', (ip_address, one_minute_ago))

            current_requests = cursor.fetchone()[0]
            remaining_requests = max(0, VISIT_LIMIT - current_requests)

            # Проверяем блокировку
            cursor.execute('''
                SELECT block_start_time, block_reason FROM ip_blocks 
                WHERE ip_address = ? AND is_blocked = TRUE
            ''', (ip_address,))

            blocked_result = cursor.fetchone()
            blocked = blocked_result is not None
            block_reason = blocked_result[1] if blocked else None

            conn.close()

            status_data = {
                'ip': ip_address,
                'current_requests': current_requests,
                'limit': VISIT_LIMIT,
                'remaining': remaining_requests,
                'blocked': blocked,
                'block_reason': block_reason,
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

    def handle_admin_login(self):
        """Обработка входа в админку"""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            if data.get('password') == MANAGE_PASSWORD:
                session_id = create_admin_session()
                self.send_response(200)
                self.send_header('Set-Cookie', f'admin_session={session_id}; Path=/; HttpOnly; Max-Age=3600')
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True}).encode())
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

            blocked_by = "admin"
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

        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Управление кланом BENZ</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #1a1a1a; color: white; }
                .container { max-width: 1200px; margin: 0 auto; }
                .header { background: #2a2a2a; color: #ff9900; padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #444; position: relative; }
                .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }
                .stat-card { background: #2a2a2a; padding: 20px; border-radius: 10px; border: 1px solid #444; max-height: 300px; overflow: hidden; }
                .stat-card div[style*="overflow-y"] { scrollbar-width: thin; scrollbar-color: #ff9900 #2a2a2a; }
                .stat-card div[style*="overflow-y"]::-webkit-scrollbar { width: 6px; }
                .stat-card div[style*="overflow-y"]::-webkit-scrollbar-track { background: #2a2a2a; border-radius: 3px; }
                .stat-card div[style*="overflow-y"]::-webkit-scrollbar-thumb { background: #ff9900; border-radius: 3px; }
                .control-panel { background: #2a2a2a; padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #444; }
                .btn { padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block; }
                .btn-primary { background: #ff9900; color: white; }
                .btn-danger { background: #e74c3c; color: white; }
                .btn-success { background: #27ae60; color: white; }
                .btn-warning { background: #f39c12; color: white; }
                .status { padding: 5px 10px; border-radius: 15px; font-size: 12px; margin-left: 10px; }
                .status-online { background: #27ae60; color: white; }
                .status-offline { background: #e74c3c; color: white; }
                .logout-btn { background: #666; color: white; float: right; }
                .back-btn { background: #444; color: white; float: left; }
                .maintenance-alert { 
                    background: #e74c3c; 
                    color: white; 
                    padding: 15px; 
                    border-radius: 5px; 
                    margin-bottom: 20px;
                    border-left: 5px solid #c0392b;
                }
                .tab { overflow: hidden; border: 1px solid #444; background-color: #2a2a2a; border-radius: 5px; margin-bottom: 20px; }
                .tab button { background-color: inherit; float: left; border: none; outline: none; cursor: pointer; padding: 14px 16px; transition: 0.3s; color: white; font-size: 16px; }
                .tab button:hover { background-color: #333; }
                .tab button.active { background-color: #ff9900; color: black; font-weight: bold; }
                .tabcontent { display: none; padding: 20px 0; }
                .applications-table { width: 100%; border-collapse: collapse; margin-top: 20px; background: #2a2a2a; border-radius: 10px; overflow: hidden; }
                .applications-table th, .applications-table td { padding: 15px; text-align: left; border-bottom: 1px solid #444; }
                .applications-table th { background: #333; color: #ff9900; font-weight: bold; }
                .applications-table tr:hover { background: #333; }
                .applications-table td { color: #e0e0e0; }
                .message-cell { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                .message-cell:hover { white-space: normal; overflow: visible; }
                .form-group { margin-bottom: 15px; }
                .form-group label { display: block; margin-bottom: 5px; color: #ff9900; font-weight: bold; }
                .form-group input, .form-group textarea, .form-group select { width: 100%; padding: 8px; background: #1a1a1a; border: 1px solid #444; border-radius: 4px; color: white; }
                .manual-blocks-list { margin-top: 20px; }
                .block-item { background: #2a2a2a; padding: 15px; margin-bottom: 10px; border-radius: 5px; border-left: 4px solid #e74c3c; }
                .block-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
                .block-ip { font-weight: bold; color: #ff9900; }
                .block-reason { color: #cccccc; }
                .block-meta { font-size: 12px; color: #999; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <a href="/" class="btn back-btn">← Вернуться на сайт</a>
                    <a href="/admin/logout" class="btn logout-btn">Выйти</a>
                    <div style="clear: both;"></div>
                    <h1>Панель управления кланом BENZ</h1>
                    <p>Мониторинг статистики и управление системой</p>
                </div>

                """ + ("""
                <div class="maintenance-alert">
                    <strong>⚠️ ВНИМАНИЕ:</strong> Режим технического обслуживания ВКЛЮЧЕН. Все пользователи видят страницу обслуживания.
                </div>
                """ if MAINTENANCE_MODE else "") + """

                <div class="tab">
                    <button class="tablinks active" onclick="openTab(event, 'Dashboard')">Дашборд</button>
                    <button class="tablinks" onclick="openTab(event, 'Applications')">Заявки</button>
                    <button class="tablinks" onclick="openTab(event, 'IPBlocks')">Блокировка IP</button>
                    <button class="tablinks" onclick="openTab(event, 'Maintenance')">Тех. обслуживание</button>
                </div>

                <div id="Dashboard" class="tabcontent" style="display: block;">
                    <div class="stats-grid" id="statsGrid">
                        <!-- Статистика будет загружена через JavaScript -->
                    </div>

                    <div class="control-panel">
                        <h2>Статус сервисов</h2>
                        <div>
                            <span id="serverStatus" class="status status-online">Сервер: Запущен</span>
                            <span class="status """ + maintenance_class + """">Обслуживание: """ + maintenance_status + """</span>
                        </div>
                    </div>
                </div>

                <div id="Applications" class="tabcontent">
                    <h2>Заявки на вступление</h2>
                    <div id="applicationsList">
                        <!-- Заявки будут загружены через JavaScript -->
                    </div>
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
                        <div id="manualBlocksList">
                            <!-- Список блокировок будет загружен через JavaScript -->
                        </div>
                    </div>
                </div>

                <div id="Maintenance" class="tabcontent">
                    <h2>Управление техническим обслуживанием</h2>

                    <div class="control-panel">
                        <h3>Текущий статус</h3>
                        <p>Режим технического обслуживания: <span class="status """ + maintenance_class + """">""" + maintenance_status + """</span></p>
                        <p>В этом режиме все посетители сайта (кроме администраторов) будут видеть страницу технического обслуживания.</p>

                        <div style="margin-top: 20px;">
                            """ + ("""
                            <button class="btn btn-success" onclick="toggleMaintenanceMode(false)">
                                🟢 Выключить режим обслуживания
                            </button>
                            <p style="margin-top: 10px; color: #27ae60;"><strong>Сайт снова будет доступен для всех пользователей</strong></p>
                            """ if MAINTENANCE_MODE else """
                            <button class="btn btn-warning" onclick="toggleMaintenanceMode(true)">
                                🔴 Включить режим обслуживания
                            </button>
                            <p style="margin-top: 10px; color: #e74c3c;"><strong>Все пользователи будут перенаправлены на страницу обслуживания</strong></p>
                            """) + """
                        </div>
                    </div>

                    <div class="control-panel">
                        <h3>Информация о режиме обслуживания</h3>
                        <ul>
                            <li>Администраторы всегда имеют доступ к сайту и админ-панели</li>
                            <li>Обычные пользователи видят страницу технического обслуживания</li>
                            <li>Форма подачи заявок временно недоступна</li>
                            <li>API endpoints продолжают работать для администраторов</li>
                        </ul>
                    </div>
                </div>
            </div>

            <script>
                function openTab(evt, tabName) {
                    var i, tabcontent, tablinks;
                    tabcontent = document.getElementsByClassName("tabcontent");
                    for (i = 0; i < tabcontent.length; i++) {
                        tabcontent[i].style.display = "none";
                    }
                    tablinks = document.getElementsByClassName("tablinks");
                    for (i = 0; i < tablinks.length; i++) {
                        tablinks[i].className = tablinks[i].className.replace(" active", "");
                    }
                    document.getElementById(tabName).style.display = "block";
                    evt.currentTarget.className += " active";

                    if (tabName === 'Applications') {
                        loadApplications();
                    } else if (tabName === 'IPBlocks') {
                        loadManualBlocks();
                    }
                }

                function updateStats() {
                    fetch('/admin/api/stats')
                        .then(response => response.json())
                        .then(data => {
                            document.getElementById('statsGrid').innerHTML = `
                                <div class="stat-card">
                                    <h3>📊 Общая статистика</h3>
                                    <p style="font-size: 24px; font-weight: bold; color: #ff9900;">${data.applications.total}</p>
                                    <p>Всего заявок</p>
                                    <div style="border-top: 1px solid #444; margin: 10px 0; padding-top: 10px;">
                                        <p>Сегодня: ${data.applications.today}</p>
                                        <p>За неделю: ${data.applications.week}</p>
                                        <p>За 24 часа: ${data.applications.daily}</p>
                                        <p>За час: ${data.applications.hour}</p>
                                    </div>
                                </div>

                                <div class="stat-card">
                                    <h3>👥 Статистика по ролям</h3>
                                    <div style="max-height: 200px; overflow-y: auto;">
                                        ${Object.entries(data.applications.roles).map(([role, count]) => `
                                            <p><strong>${role}:</strong> ${count}</p>
                                        `).join('')}
                                    </div>
                                    <div style="border-top: 1px solid #444; margin: 10px 0; padding-top: 10px;">
                                        <p><strong>Популярная роль:</strong> ${data.applications.popular_role}</p>
                                        <p><strong>Средние часы:</strong> ${data.applications.avg_playtime}</p>
                                    </div>
                                </div>

                                <div class="stat-card">
                                    <h3>🌐 Посещения</h3>
                                    <p style="font-size: 24px; font-weight: bold; color: #ff9900;">${data.visits.total_visits}</p>
                                    <p>Всего посещений</p>
                                    <div style="border-top: 1px solid #444; margin: 10px 0; padding-top: 10px;">
                                        <p>Уникальных: ${data.visits.unique_visitors}</p>
                                        <p>Сегодня: ${data.visits.today_visits}</p>
                                    </div>
                                </div>

                                <div class="stat-card">
                                    <h3>⚙️ Система</h3>
                                    <p><strong>Сервер:</strong> <span class="status status-online">${data.services.server}</span></p>
                                    <p><strong>Порт сервера:</strong> ${data.services.server_port}</p>
                                    <p><strong>База данных:</strong> <span class="status status-online">${data.services.database}</span></p>
                                    <p><strong>Активные сессии:</strong> ${data.system.active_sessions}</p>
                                    <p><strong>Обновлено:</strong> ${data.system.timestamp}</p>
                                </div>
                            `;

                            document.getElementById('serverStatus').className = 'status status-online';
                            document.getElementById('serverStatus').textContent = 'Сервер: Запущен';
                        });
                }

                function loadApplications() {
                    fetch('/admin/api/applications')
                        .then(response => response.json())
                        .then(data => {
                            const applicationsList = document.getElementById('applicationsList');
                            if (data.applications.length === 0) {
                                applicationsList.innerHTML = '<div class="stat-card"><p>Нет заявок</p></div>';
                                return;
                            }

                            let html = `
                                <table class="applications-table">
                                    <thead>
                                        <tr>
                                            <th>ID</th>
                                            <th>Никнейм</th>
                                            <th>Steam ID</th>
                                            <th>Часы</th>
                                            <th>Discord</th>
                                            <th>Роль</th>
                                            <th>Сообщение</th>
                                            <th>IP</th>
                                            <th>Дата</th>
                                            <th>Статус</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                            `;

                            data.applications.forEach(app => {
                                html += `
                                    <tr>
                                        <td>${app.id}</td>
                                        <td><strong>${app.nickname}</strong></td>
                                        <td>${app.steam_id}</td>
                                        <td>${app.playtime}</td>
                                        <td>${app.discord}</td>
                                        <td>${app.role}</td>
                                        <td class="message-cell" title="${app.message}">${app.message}</td>
                                        <td>${app.ip_address}</td>
                                        <td>${new Date(app.timestamp).toLocaleString('ru-RU')}</td>
                                        <td><span class="status ${app.status === 'new' ? 'status-online' : 'status-offline'}">${app.status}</span></td>
                                    </tr>
                                `;
                            });

                            html += '</tbody></table>';
                            applicationsList.innerHTML = html;
                        });
                }

                function loadManualBlocks() {
                    fetch('/admin/api/manual-blocks')
                        .then(response => response.json())
                        .then(data => {
                            const blocksList = document.getElementById('manualBlocksList');
                            if (data.blocks.length === 0) {
                                blocksList.innerHTML = '<div class="stat-card"><p>Нет активных блокировок</p></div>';
                                return;
                            }

                            let html = '';
                            data.blocks.forEach(block => {
                                const blockTime = new Date(block.block_time).toLocaleString('ru-RU');
                                const expiresInfo = block.expires_at ? 
                                    `Истекает: ${new Date(block.expires_at).toLocaleString('ru-RU')}` : 
                                    'Блокировка постоянная';

                                html += `
                                    <div class="block-item">
                                        <div class="block-header">
                                            <span class="block-ip">${block.ip_address}</span>
                                            <button class="btn btn-success" onclick="unblockIP('${block.ip_address}')">Разблокировать</button>
                                        </div>
                                        <div class="block-reason">${block.reason || 'Причина не указана'}</div>
                                        <div class="block-meta">
                                            Заблокирован: ${blockTime} | ${expiresInfo} | Администратор: ${block.blocked_by}
                                            ${block.is_expired ? ' <span style="color: #ff9900;">(Истекла)</span>' : ''}
                                        </div>
                                    </div>
                                `;
                            });

                            blocksList.innerHTML = html;
                        });
                }

                function unblockIP(ipAddress) {
                    if (confirm(`Вы уверены, что хотите разблокировать IP ${ipAddress}?`)) {
                        fetch('/admin/api/manual-blocks/remove', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({ ip_address: ipAddress })
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                alert('IP разблокирован');
                                loadManualBlocks();
                            } else {
                                alert('Ошибка при разблокировке IP');
                            }
                        });
                    }
                }

                function toggleMaintenanceMode(enable) {
                    const action = enable ? 'включить' : 'выключить';
                    if (confirm(`Вы уверены, что хотите ${action} режим технического обслуживания?`)) {
                        fetch('/admin/api/maintenance/toggle', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({ enabled: enable })
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                alert(`Режим обслуживания ${enable ? 'включен' : 'выключен'}`);
                                location.reload();
                            } else {
                                alert('Ошибка при изменении режима обслуживания');
                            }
                        })
                        .catch(error => {
                            console.error('Error:', error);
                            alert('Ошибка при изменении режима обслуживания');
                        });
                    }
                }

                // Обработчик формы блокировки IP
                document.getElementById('blockIpForm').addEventListener('submit', function(e) {
                    e.preventDefault();

                    const formData = {
                        ip_address: document.getElementById('ip_address').value,
                        block_reason: document.getElementById('block_reason').value,
                        expires_hours: document.getElementById('expires_hours').value
                    };

                    fetch('/admin/api/manual-blocks/add', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify(formData)
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            alert('IP успешно заблокирован');
                            document.getElementById('blockIpForm').reset();
                            loadManualBlocks();
                        } else {
                            alert('Ошибка при блокировке IP: ' + data.message);
                        }
                    });
                });

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
                    const password = document.getElementById('password').value;

                    const response = await fetch('/admin/api/login', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ password: password })
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
        server_httpd = HTTPServer(server_address, ClanRequestHandler)
        logger.info(f"Сервер запущен на порту {SERVER_PORT}")
        logger.info(f"Админка доступна по адресу: http://localhost:{SERVER_PORT}/admin")
        logger.info(f"Пароль для входа в админку: {MANAGE_PASSWORD}")
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
    print(f"Пароль для входа: {MANAGE_PASSWORD}")
    print(f"Режим обслуживания: {'ВКЛЮЧЕН' if MAINTENANCE_MODE else 'ВЫКЛЮЧЕН'}")
    print("\nДля остановки нажмите Ctrl+C")
    print("=" * 50)

    # Запуск сервера (блокирующий вызов)
    run_server()


if __name__ == '__main__':
    main()