import sqlite3
import time
from config import DATABASE_NAME

def get_connection():
    return sqlite3.connect(DATABASE_NAME)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # ۱. جدول کاربران، نقش‌ها و آمار تفکیکی
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER,
            chat_id INTEGER,
            role TEXT DEFAULT 'user',
            warns INTEGER DEFAULT 0,
            asl TEXT DEFAULT 'ثبت نشده',
            nickname TEXT DEFAULT '',
            msgs_today INTEGER DEFAULT 0,
            msgs_total INTEGER DEFAULT 0,
            photos_count INTEGER DEFAULT 0,
            videos_count INTEGER DEFAULT 0,
            voices_count INTEGER DEFAULT 0,
            stickers_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, chat_id)
        )
    ''')
    
    # ۲. جدول مالکین و مدیران (Owners & Admins)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS roles (
            user_id INTEGER,
            chat_id INTEGER,
            role_type TEXT, -- 'owner', 'admin', 'special', 'exempt'
            title TEXT DEFAULT '',
            PRIMARY KEY (user_id, chat_id)
        )
    ''')

    # ۳. جدول ادمین‌های زمان‌دار (Temp Admins)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS temp_admins (
            user_id INTEGER,
            chat_id INTEGER,
            expire_timestamp INTEGER,
            PRIMARY KEY (user_id, chat_id)
        )
    ''')

    # ۴. جدول تنظیمات کامل قفل‌ها، خاموشی و اخطار
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id INTEGER PRIMARY KEY,
            max_warns INTEGER DEFAULT 3,
            warn_action TEXT DEFAULT 'mute',
            lock_photo BOOLEAN DEFAULT 0,
            lock_link BOOLEAN DEFAULT 0,
            lock_sticker BOOLEAN DEFAULT 0,
            lock_voice BOOLEAN DEFAULT 0,
            lock_video BOOLEAN DEFAULT 0,
            lock_forward BOOLEAN DEFAULT 0,
            lock_gif BOOLEAN DEFAULT 0,
            lock_text BOOLEAN DEFAULT 0,
            shutdown_mode INTEGER DEFAULT 0, -- 0: off, 1: lock group, 2: absolute, 3: del text, 4: del media
            shutdown_until INTEGER DEFAULT 0,
            rules_text TEXT DEFAULT 'قوانینی برای گروه مربوطه ثبت نشده است!',
            anti_betrayal_limit INTEGER DEFAULT 5
        )
    ''')
    
    # ۵. جدول فیلتر کلمات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filtered_words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            word TEXT,
            action TEXT DEFAULT 'del'
        )
    ''')
    
    # ۶. جدول پاسخ‌های خودکار (Auto Reply Wizard)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auto_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            keyword TEXT,
            reply_text TEXT
        )
    ''')

    # ۷. جدول کانال‌های عضویت اجباری
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS force_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            channel_username TEXT
        )
    ''')

    conn.commit()
    conn.close()

# --- توابع مدیریت نقش‌ها و سطوح دسترسی ---

def set_user_role(chat_id: int, user_id: int, role_type: str, title: str = ""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO roles (user_id, chat_id, role_type, title) 
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, chat_id) DO UPDATE SET role_type=?, title=?
    ''', (user_id, chat_id, role_type, title, role_type, title))
    conn.commit()
    conn.close()

def remove_user_role(chat_id: int, user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM roles WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    cursor.execute("DELETE FROM temp_admins WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    conn.commit()
    conn.close()

def get_user_role(chat_id: int, user_id: int) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role_type FROM roles WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "user"

def add_temp_admin(chat_id: int, user_id: int, duration_seconds: int):
    expire_time = int(time.time()) + duration_seconds
    set_user_role(chat_id, user_id, "admin")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO temp_admins (user_id, chat_id, expire_timestamp)
        VALUES (?, ?, ?)
    ''', (user_id, chat_id, expire_time))
    conn.commit()
    conn.close()

def get_expired_temp_admins():
    now = int(time.time())
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, chat_id FROM temp_admins WHERE expire_timestamp <= ?", (now,))
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- توابع تنظیمات و قفل‌ها ---

def get_group_settings(chat_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM group_settings WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO group_settings (chat_id) VALUES (?)", (chat_id,))
        conn.commit()
        cursor.execute("SELECT * FROM group_settings WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
    conn.close()
    return {
        "max_warns": row[1],
        "warn_action": row[2],
        "lock_photo": bool(row[3]),
        "lock_link": bool(row[4]),
        "lock_sticker": bool(row[5]),
        "lock_voice": bool(row[6]),
        "lock_video": bool(row[7]),
        "lock_forward": bool(row[8]),
        "lock_gif": bool(row[9]),
        "lock_text": bool(row[10]),
        "shutdown_mode": row[11],
        "shutdown_until": row[12],
        "rules_text": row[13],
        "anti_betrayal_limit": row[14]
    }

def update_setting(chat_id: int, key: str, value):
    conn = get_connection()
    cursor = conn.cursor()
    get_group_settings(chat_id)
    cursor.execute(f"UPDATE group_settings SET {key} = ? WHERE chat_id = ?", (value, chat_id))
    conn.commit()
    conn.close()

# --- توابع اخطار، فیلتر و آمار ---

def add_warn(chat_id: int, user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, chat_id, warns) VALUES (?, ?, 0)", (user_id, chat_id))
    cursor.execute("UPDATE users SET warns = warns + 1 WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    cursor.execute("SELECT warns FROM users WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    warns = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return warns

def reset_warns(chat_id: int, user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET warns = 0 WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()