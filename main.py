import logging
import asyncio
from datetime import datetime
import pytz
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from config import BOT_TOKEN, SUPER_OWNER_ID, PORT
from database import (
    init_db, add_warn, reset_warns, get_group_settings, update_setting
)
from roles import (
    check_user_level, set_owner_command, set_admin_command,
    demote_command, check_temp_admins_job
)
from locks import process_group_locks, record_admin_action
from features import (
    start_auto_reply_wizard, process_auto_reply_wizard, check_auto_replies,
    start_filter_wizard, process_filter_wizard, build_warn_pad_keyboard,
    warn_settings_command
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def handle_ping(request):
    return web.Response(text="Mafioso Bot is Fully Active and Alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Pinger web server running on port {PORT}")

def get_persian_date_info():
    tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tz)
    
    day_of_year = now.timetuple().tm_yday
    total_days = 366 if (now.year % 4 == 0 and (now.year % 100 != 0 or now.year % 400 == 0)) else 365
    passed_pct = round((day_of_year / total_days) * 100, 2)
    remain_pct = round(100 - passed_pct, 2)
    remain_days = total_days - day_of_year

    weekdays_fa = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه", "شنبه", "یکشنبه"]
    weekday_str = weekdays_fa[now.weekday()]

    text = (
        f"🕒 **ساعت و تاریخ :**\n\n"
        f"• ساعت : {now.strftime('%H:%M')}\n"
        f"• تاریخ امروز : {weekday_str} – {now.day} / {now.month} / {now.year}\n"
        f"• تاریخ میلادی : {now.strftime('%A – %Y %d %B')}\n\n"
        f"• روز های سپری شده : {day_of_year} روز ( {passed_pct} درصد )\n"
        f"• روز های باقی مانده : {remain_days} روز ( {remain_pct} درصد )"
    )
    return text

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("• راهنمای دستورات", callback_data=f"help_cmds:{user_id}")],
        [InlineKeyboardButton("• راهنمای تنظیمات پیشرفته", callback_data=f"help_advanced:{user_id}")],
        [InlineKeyboardButton("• راهنمای سرگرمی و کاربردی", callback_data=f"help_fun:{user_id}")]
    ]
    await update.message.reply_text(
        "📚 **راهنمای ربات صفحه اصلی :**\nاز هر بخش منوی زیر می‌توانید استفاده کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def date_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_persian_date_info(), parse_mode="Markdown")

# --- سیستم اخطار هوشمند با Suppress Warning ---
async def warn_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("⚠️ برای دادن اخطار لطفاً روی پیام کاربر مورد نظر ریپلی کنید.")
        return
    
    sender_id = msg.from_user.id
    chat_id = update.effective_chat.id
    
    role = await check_user_level(chat_id, sender_id)
    if role not in ["super_owner", "owner", "admin"]:
        await msg.reply_text("⚠️ شما دسترسی لازم برای اخطار دادن را ندارید.")
        return

    # چک کردن سیستم ضد خیانت برای ادمین صادرکننده دستور
    if not await record_admin_action(chat_id, sender_id, context):
        return

    target_user = msg.reply_to_message.from_user
    settings = get_group_settings(chat_id)
    max_warns = settings["max_warns"]
    
    current_warns = add_warn(chat_id, target_user.id)

    # اخطار نهایی -> سرکوب پیام اخطار و اجرای آنی مجازات
    if current_warns >= max_warns:
        try:
            if settings["warn_action"] == "ban":
                await context.bot.ban_chat_member(chat_id, target_user.id)
                action_text = "بن (Ban)"
            else:
                permissions = ChatPermissions(can_send_messages=False)
                await context.bot.restrict_chat_member(chat_id, target_user.id, permissions=permissions)
                action_text = "سکوت (Mute)"
                
            reset_warns(chat_id, target_user.id)
            # خروجی خلاصه مجازات بدون پیام اخطار تکراری
            await msg.reply_text(
                f"🚨 کاربر <a href='tg://user?id={target_user.id}'>{target_user.first_name}</a> به دلیل تکمیل شدن اخطارهای خود ({max_warns}/{max_warns}) از گروه **{action_text}** شد.",
                parse_mode="HTML"
            )
        except Exception as e:
            await msg.reply_text(f"❌ خطا در اعمال مجازات کاربر: {e}")
    else:
        await msg.reply_text(
            f"⚠️ کاربر <a href='tg://user?id={target_user.id}'>{target_user.first_name}</a> شما یک اخطار دریافت کردید!\n📊 تعداد اخطارهای شما: {current_warns}/{max_warns}",
            parse_mode="HTML"
        )

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat.id

    parts = data.split(":")
    action = parts[0]
    
    if len(parts) > 1 and parts[-1].isdigit():
        panel_owner_id = int(parts[-1])
        if user_id != panel_owner_id and user_id != SUPER_OWNER_ID:
            await query.answer("⚠️ این پنل برای شما نیست!", show_alert=True)
            return

    if action == "close_menu":
        await query.message.delete()
    
    elif action == "setwarn":
        new_max = int(parts[1])
        update_setting(chat_id, "max_warns", new_max)
        markup = build_warn_pad_keyboard(chat_id, user_id)
        await query.edit_message_reply_markup(reply_markup=markup)
        await query.answer(f"سقف اخطار به {new_max} تغییر یافت.")

    elif action == "setwarnact":
        new_act = parts[1]
        update_setting(chat_id, "warn_action", new_act)
        markup = build_warn_pad_keyboard(chat_id, user_id)
        await query.edit_message_reply_markup(reply_markup=markup)
        await query.answer(f"نوع مجازات به {new_act} تغییر یافت.")

    else:
        await query.answer("این بخش فعال است.")

async def central_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await process_filter_wizard(update, context):
        return
    if await process_auto_reply_wizard(update, context):
        return
    await process_group_locks(update, context)
    await check_auto_replies(update, context)

# ==========================================
# 🚀 ۷. اجرای ربات و ثبت هندلرها (Main Entry)
# ==========================================
async def post_init(application: Application):
    """راه‌اندازی وب‌سرور Pinger همزمان با استارت ربات"""
    asyncio.create_task(start_web_server())

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", help_command))
    app.add_handler(MessageHandler(filters.Regex("^تنظیم مالک"), set_owner_command))
    app.add_handler(MessageHandler(filters.Regex("^تنظیم مدیر"), set_admin_command))
    app.add_handler(MessageHandler(filters.Regex("^(عزل|حذف مدیر|حذف مالک)"), demote_command))

    app.add_handler(MessageHandler(filters.Regex("^راهنما$"), help_command))
    app.add_handler(MessageHandler(filters.Regex("^(تاریخ|زمان|ساعت)$"), date_command))
    app.add_handler(MessageHandler(filters.Regex("^اخطار$"), warn_user_command))
    app.add_handler(MessageHandler(filters.Regex("^تنظیم اخطار$"), warn_settings_command))
    app.add_handler(MessageHandler(filters.Regex("^تنظیم پاسخ خودکار$"), start_auto_reply_wizard))

    app.add_handler(CallbackQueryHandler(button_callback_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, central_message_handler), group=1)

    if app.job_queue:
        app.job_queue.run_repeating(check_temp_admins_job, interval=60, first=10)

    print("🤖 Mafioso Bot is 100% complete, patched, and active!")
    app.run_polling()

if __name__ == "__main__":
    main()
