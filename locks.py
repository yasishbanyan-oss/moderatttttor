import time
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import SUPER_OWNER_ID
from database import (
    get_group_settings, update_setting, get_filtered_words,
    get_connection
)
from roles import check_user_level, remove_user_role

logger = logging.getLogger(__name__)

# حافظه موقت در مموری برای شمارش اکشن‌های ادمین‌ها (ضد خیانت)
ADMIN_ACTIONS = {}

async def record_admin_action(chat_id: int, admin_id: int, context: ContextTypes.DEFAULT_TYPE):
    """سیستم ضد خیانت: مانیتورینگ اکشن‌های بن/محدودسازی ادمین‌ها"""
    role = await check_user_level(chat_id, admin_id)
    if role in ["super_owner", "owner"]:
        return True  # مالکین معاف هستند

    settings = get_group_settings(chat_id)
    limit = settings.get("anti_betrayal_limit", 5)

    now = time.time()
    key = (chat_id, admin_id)
    
    if key not in ADMIN_ACTIONS:
        ADMIN_ACTIONS[key] = []

    # پاکسازی اکشن‌های قدیمی‌تر از ۱۰ دقیقه
    ADMIN_ACTIONS[key] = [t for t in ADMIN_ACTIONS[key] if now - t < 600]
    ADMIN_ACTIONS[key].append(now)

    if len(ADMIN_ACTIONS[key]) >= limit:
        # عزل آنی ادمین متخلف
        remove_user_role(chat_id, admin_id)
        try:
            await context.bot.promote_chat_member(
                chat_id=chat_id, user_id=admin_id,
                can_delete_messages=False, can_invite_users=False,
                can_restrict_members=False, can_pin_messages=False
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 **سیستم ضد خیانت فعال شد!**\nمدیر <a href='tg://user?id={admin_id}'>کاربر</a> به دلیل بن/سکوت دسته‌جمعی و فراتر از حد مجاز، بلافاصله عزل شد.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in anti-betrayal demote: {e}")
        return False

    return True

# --- مابقی توابع locks.py (بدون تغییر) ---

def add_force_channel(chat_id: int, channel_username: str):
    conn = get_connection()
    cursor = conn.cursor()
    channel_clean = channel_username.replace("@", "").strip()
    cursor.execute("INSERT INTO force_channels (chat_id, channel_username) VALUES (?, ?)", (chat_id, channel_clean))
    conn.commit()
    conn.close()

def get_force_channels(chat_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_username FROM force_channels WHERE chat_id = ?", (chat_id,))
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.message
    if not msg or not msg.from_user:
        return True

    chat_id = update.effective_chat.id
    user_id = msg.from_user.id

    role = await check_user_level(chat_id, user_id)
    if role in ["super_owner", "owner", "admin", "exempt"]:
        return True

    channels = get_force_channels(chat_id)
    if not channels:
        return True

    not_joined = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(f"@{ch}", user_id)
            if member.status in ["left", "kicked"]:
                not_joined.append(ch)
        except Exception:
            pass

    if not_joined:
        try:
            await msg.delete()
        except Exception:
            pass

        buttons = [[InlineKeyboardButton(f"📢 عضویت در @{ch}", url=f"https://t.me/{ch}")] for ch in not_joined]
        markup = InlineKeyboardMarkup(buttons)
        
        warn_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ کاربر <a href='tg://user?id={user_id}'>{msg.from_user.first_name}</a> جهت فعالیت در گروه باید در کانال‌های زیر عضو شوید:",
            reply_markup=markup,
            parse_mode="HTML"
        )
        context.job_queue.run_once(lambda c: c.bot.delete_message(chat_id, warn_msg.message_id), 15)
        return False

    return True

async def process_group_locks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return

    chat_id = update.effective_chat.id
    user_id = msg.from_user.id
    role = await check_user_level(chat_id, user_id)

    if role == "super_owner":
        return

    settings = get_group_settings(chat_id)
    now = int(time.time())

    shutdown_mode = settings["shutdown_mode"]
    shutdown_until = settings["shutdown_until"]

    if shutdown_mode > 0:
        if shutdown_until == 0 or now < shutdown_until:
            if shutdown_mode == 1 and role not in ["owner", "admin", "special"]:
                await _safe_delete(msg)
                return
            elif shutdown_mode == 2:
                await _safe_delete(msg)
                return
            elif shutdown_mode == 3 and (msg.text or msg.caption):
                if role not in ["owner", "admin", "special"]:
                    await _safe_delete(msg)
                    return
            elif shutdown_mode == 4 and (msg.photo or msg.video or msg.voice or msg.document or msg.sticker or msg.animation):
                if role not in ["owner", "admin", "special"]:
                    await _safe_delete(msg)
                    return
        elif shutdown_until > 0 and now >= shutdown_until:
            update_setting(chat_id, "shutdown_mode", 0)
            update_setting(chat_id, "shutdown_until", 0)

    if role in ["owner", "admin", "special"]:
        return

    is_joined = await check_force_join(update, context)
    if not is_joined:
        return

    should_delete = False

    if settings["lock_photo"] and msg.photo:
        should_delete = True
    elif settings["lock_link"] and (msg.entities and any(e.type in ["url", "text_link"] for e in msg.entities)):
        should_delete = True
    elif settings["lock_sticker"] and msg.sticker:
        should_delete = True
    elif settings["lock_voice"] and msg.voice:
        should_delete = True
    elif settings["lock_video"] and msg.video:
        should_delete = True
    elif settings["lock_forward"] and msg.forward_date:
        should_delete = True
    elif settings["lock_gif"] and msg.animation:
        should_delete = True
    elif settings["lock_text"] and (msg.text and not msg.entities):
        should_delete = True

    text_content = msg.text or msg.caption
    if not should_delete and text_content:
        words = get_filtered_words(chat_id)
        for w, action in words:
            if re.search(r'\b' + re.escape(w) + r'\b', text_content, re.IGNORECASE):
                should_delete = True
                break

    if should_delete:
        await _safe_delete(msg)

async def _safe_delete(msg):
    try:
        await msg.delete()
    except Exception as e:
        logger.debug(f"Failed to delete message: {e}")