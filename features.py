import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import ContextTypes

from database import (
    get_connection, get_group_settings, update_setting, 
    add_warn, reset_warns, add_filtered_word, get_filtered_words
)
from roles import check_user_level

logger = logging.getLogger(__name__)

# ساخت حافظه موقت در مموری برای ویزاردها (ویزارد پاسخ خودکار و فیلتر کلمات)
WIZARD_STATE = {}

# ==========================================
# 🤖 ۱. سیستم پاسخ خودکار (Auto Reply Wizard)
# ==========================================

def add_auto_reply(chat_id: int, keyword: str, reply_text: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO auto_replies (chat_id, keyword, reply_text) VALUES (?, ?, ?)",
                   (chat_id, keyword.strip().lower(), reply_text.strip()))
    conn.commit()
    conn.close()

def get_auto_replies(chat_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT keyword, reply_text FROM auto_replies WHERE chat_id = ?", (chat_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_auto_reply(chat_id: int, keyword: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_replies WHERE chat_id = ? AND keyword = ?", (chat_id, keyword.strip().lower()))
    conn.commit()
    conn.close()

async def start_auto_reply_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = update.effective_chat.id
    user_id = msg.from_user.id

    role = await check_user_level(chat_id, user_id)
    if role not in ["super_owner", "owner", "admin"]:
        await msg.reply_text("⚠️ شما دسترسی لازم برای تنظیم پاسخ خودکار را ندارید.")
        return

    WIZARD_STATE[(chat_id, user_id)] = {"step": "WAITING_KEYWORD"}
    await msg.reply_text("🤖 **ویزارد تنظیم پاسخ خودکار:**\nلطفاً کلمه یا عبارت کلیدی (Keyword) مورد نظر را ارسال کنید:")

async def process_auto_reply_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.message
    if not msg or not msg.text:
        return False

    chat_id = update.effective_chat.id
    user_id = msg.from_user.id
    key = (chat_id, user_id)

    if key not in WIZARD_STATE:
        return False

    state = WIZARD_STATE[key]

    if state["step"] == "WAITING_KEYWORD":
        state["keyword"] = msg.text
        state["step"] = "WAITING_REPLY"
        await msg.reply_text(f"✅ کلمه کلیدی ثبت شد: **{msg.text}**\n\nحالا متن پاسخی که ربات باید ارسال کند را وارد کنید:")
        return True

    elif state["step"] == "WAITING_REPLY":
        keyword = state["keyword"]
        reply_text = msg.text
        add_auto_reply(chat_id, keyword, reply_text)
        del WIZARD_STATE[key]

        await msg.reply_text(f"🎉 **پاسخ خودکار با موفقیت ثبت شد!**\n\n🔹 **کلمه:** {keyword}\n🔸 **پاسخ:** {reply_text}")
        return True

    return False

async def check_auto_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    chat_id = update.effective_chat.id
    text = msg.text.lower()

    replies = get_auto_replies(chat_id)
    for kw, reply in replies:
        if kw in text:
            await msg.reply_text(reply, reply_to_message_id=msg.message_id)
            break

# ==========================================
# ⛔️ ۲. سیستم فیلتر کلمات چندتایی با `/done`
# ==========================================

async def start_filter_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str = "del"):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    WIZARD_STATE[(chat_id, user_id)] = {
        "step": "WAITING_WORDS",
        "type": "FILTER_WORDS",
        "action": action,
        "words": []
    }

async def process_filter_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.message
    if not msg or not msg.text:
        return False

    chat_id = update.effective_chat.id
    user_id = msg.from_user.id
    key = (chat_id, user_id)

    if key not in WIZARD_STATE or WIZARD_STATE[key].get("type") != "FILTER_WORDS":
        return False

    text = msg.text.strip()

    if text == "/done":
        words = WIZARD_STATE[key]["words"]
        action = WIZARD_STATE[key]["action"]
        del WIZARD_STATE[key]

        if not words:
            await msg.reply_text("⚠️ هیچ کلمه‌ای وارد نشد. عملیات لغو شد.")
        else:
            for w in words:
                add_filtered_word(chat_id, w, action)
            await msg.reply_text(f"✅ تعداد **{len(words)}** کلمه با موفقیت به لیست فیلتر اضافه شد.")
        return True

    # افزودن کلمه به لیست موقت
    WIZARD_STATE[key]["words"].append(text)
    await msg.reply_text(f"➕ کلمه **«{text}»** ثبت شد.\nکلمه بعدی را بفرستید یا برای اتمام اتمام عبارت `/done` را ارسال کنید.")
    return True

# ==========================================
# ⚠️ ۳. پد عددی ۱ تا ۱۰ تنظیم سقف اخطار
# ==========================================

def build_warn_pad_keyboard(chat_id: int, owner_id: int):
    settings = get_group_settings(chat_id)
    current_max = settings["max_warns"]
    current_action = settings["warn_action"]

    # سطر اول دکمه‌های ۱ تا ۵
    row1 = [
        InlineKeyboardButton(f"{'✅ ' if current_max == i else ''}{i}", callback_data=f"setwarn:{i}:{owner_id}")
        for i in range(1, 6)
    ]
    # سطر دوم دکمه‌های ۶ تا ۱۰
    row2 = [
        InlineKeyboardButton(f"{'✅ ' if current_max == i else ''}{i}", callback_data=f"setwarn:{i}:{owner_id}")
        for i in range(6, 11)
    ]

    act_ban = "🟢 بن (Ban)" if current_action == "ban" else "⚪️ بن (Ban)"
    act_mute = "🟢 سکوت (Mute)" if current_action == "mute" else "⚪️ سکوت (Mute)"

    row3 = [
        InlineKeyboardButton(act_ban, callback_data=f"setwarnact:ban:{owner_id}"),
        InlineKeyboardButton(act_mute, callback_data=f"setwarnact:mute:{owner_id}")
    ]

    row4 = [InlineKeyboardButton("❌ بستن", callback_data=f"close_menu:{owner_id}")]

    return InlineKeyboardMarkup([row1, row2, row3, row4])

async def warn_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    role = await check_user_level(chat_id, user_id)
    if role not in ["super_owner", "owner"]:
        await update.message.reply_text("⚠️ **تنها مالکین گروه امکان تغییر سقف اخطار را دارند.**")
        return

    markup = build_warn_pad_keyboard(chat_id, user_id)
    await update.message.reply_text(
        "⚠️ **تنظیمات سقف اخطار و مجازات گروه:**\nسقف اخطار مورد نظر و نوع مجازات نهایی را انتخاب کنید:",
        reply_markup=markup,
        parse_mode="Markdown"
    )