import logging
import time
from telegram import Update
from telegram.ext import ContextTypes

from config import SUPER_OWNER_ID
from database import (
    get_user_role, set_user_role, remove_user_role, 
    add_temp_admin, get_expired_temp_admins
)

logger = logging.getLogger(__name__)

async def check_user_level(chat_id: int, user_id: int) -> str:
    if user_id == SUPER_OWNER_ID:
        return "super_owner"
    return get_user_role(chat_id, user_id)

async def extract_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تابع کمکی برای استخراج کاربر از طریق Reply، ID یا Username"""
    msg = update.message
    chat_id = update.effective_chat.id

    if msg.reply_to_message:
        return msg.reply_to_message.from_user

    if context.args:
        raw_arg = context.args[0].strip()
        # اگر آیدی عددی باشد
        if raw_arg.isdigit() or (raw_arg.startswith("-") and raw_arg[1:].isdigit()):
            try:
                member = await context.bot.get_chat_member(chat_id, int(raw_arg))
                return member.user
            except Exception:
                pass
        # اگر یوزرنیم باشد
        elif raw_arg.startswith("@"):
            try:
                member = await context.bot.get_chat_member(chat_id, raw_arg)
                return member.user
            except Exception:
                pass

    return None

async def set_owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    sender_id = msg.from_user.id
    chat_id = update.effective_chat.id

    if sender_id != SUPER_OWNER_ID:
        await msg.reply_text("⚠️ **شما دسترسی لازم برای تنظیم مالک گروه را ندارید.**", parse_mode="Markdown")
        return

    target_user = await extract_target_user(update, context)
    if not target_user:
        await msg.reply_text("⚠️ لطفاً روی پیام کاربر ریپلی کنید یا آیدی عددی/یوزرنیم او را وارد کنید.")
        return

    set_user_role(chat_id, target_user.id, "owner")
    
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id, user_id=target_user.id,
            can_change_info=True, can_delete_messages=True,
            can_invite_users=True, can_restrict_members=True,
            can_pin_messages=True, can_promote_members=False
        )
    except Exception as e:
        logger.error(f"Error promoting owner: {e}")

    await msg.reply_text(
        f"👑 کاربر <a href='tg://user?id={target_user.id}'>{target_user.first_name}</a> به عنوان **مالک گروه** منصوب شد.",
        parse_mode="HTML"
    )

async def set_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    sender_id = msg.from_user.id
    chat_id = update.effective_chat.id

    sender_level = await check_user_level(chat_id, sender_id)
    if sender_level not in ["super_owner", "owner"]:
        await msg.reply_text("⚠️ **فقط مالکین گروه می‌توانند مدیر جدید تنظیم کنند.**", parse_mode="Markdown")
        return

    target_user = await extract_target_user(update, context)
    if not target_user:
        await msg.reply_text("⚠️ لطفاً روی پیام کاربر ریپلی کنید یا آیدی/یوزرنیم او را همراه دستور بفرستید.")
        return

    duration_seconds = 0
    # بررسی زمان‌دار بودن (مثال: 24h یا 7d در آرگومان دوم)
    if len(context.args) > 1:
        arg = context.args[1].lower()
        if arg.endswith("h") and arg[:-1].isdigit():
            duration_seconds = int(arg[:-1]) * 3600
        elif arg.endswith("d") and arg[:-1].isdigit():
            duration_seconds = int(arg[:-1]) * 86400

    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id, user_id=target_user.id,
            can_delete_messages=True, can_invite_users=True,
            can_restrict_members=True, can_pin_messages=True
        )
    except Exception as e:
        await msg.reply_text(f"❌ خطا در ارتقای مقام تلگرام: {e}")
        return

    if duration_seconds > 0:
        add_temp_admin(chat_id, target_user.id, duration_seconds)
        hours = duration_seconds // 3600
        await msg.reply_text(
            f"👮‍♂️ کاربر <a href='tg://user?id={target_user.id}'>{target_user.first_name}</a> به مدت **{hours} ساعت** به عنوان **مدیر زمان‌دار** منصوب شد.",
            parse_mode="HTML"
        )
    else:
        set_user_role(chat_id, target_user.id, "admin")
        await msg.reply_text(
            f"👮‍♂️ کاربر <a href='tg://user?id={target_user.id}'>{target_user.first_name}</a> به عنوان **مدیر دائمی گروه** منصوب شد.",
            parse_mode="HTML"
        )

async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    sender_id = msg.from_user.id
    chat_id = update.effective_chat.id

    sender_level = await check_user_level(chat_id, sender_id)
    if sender_level not in ["super_owner", "owner"]:
        await msg.reply_text("⚠️ دسترسی کافی ندارید.")
        return

    target_user = await extract_target_user(update, context)
    if not target_user:
        await msg.reply_text("⚠️ روی پیام کاربر ریپلی کنید یا آیدی/یوزرنیم او را وارد کنید.")
        return

    target_level = await check_user_level(chat_id, target_user.id)

    if target_level == "super_owner":
        await msg.reply_text("❌ شما نمی‌توانید مالک اصلی ربات را عزل کنید!")
        return
    if sender_level == "owner" and target_level == "owner":
        await msg.reply_text("❌ مالکین نمی‌توانند سایر مالکین را عزل کنند!")
        return

    remove_user_role(chat_id, target_user.id)
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id, user_id=target_user.id,
            can_change_info=False, can_delete_messages=False,
            can_invite_users=False, can_restrict_members=False,
            can_pin_messages=False, can_promote_members=False
        )
    except Exception as e:
        logger.error(f"Error demoting user: {e}")

    await msg.reply_text(
        f"👤 کاربر <a href='tg://user?id={target_user.id}'>{target_user.first_name}</a> از تمامی اختیارات عزل شد.",
        parse_mode="HTML"
    )

async def check_temp_admins_job(context: ContextTypes.DEFAULT_TYPE):
    expired_list = get_expired_temp_admins()
    for user_id, chat_id in expired_list:
        remove_user_role(chat_id, user_id)
        try:
            await context.bot.promote_chat_member(
                chat_id=chat_id, user_id=user_id,
                can_delete_messages=False, can_invite_users=False,
                can_restrict_members=False, can_pin_messages=False
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ مدت زمان ادمینی کاربر <a href='tg://user?id={user_id}'>کاربر</a> به پایان رسید و عزل شد.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error auto-demoting expired admin {user_id}: {e}")