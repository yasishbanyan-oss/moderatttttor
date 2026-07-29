import os

# 🗝 توکن اختصاصی ربات
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8989176817:AAHBHAOorua7GAZcTm4fmCQsD7tAEVxLiJk")

# 👑 شناسه عددی مالک کل ربات (غیرقابل عزل و دارای دسترسی مطلق)
SUPER_OWNER_ID = int(os.environ.get("SUPER_OWNER_ID", "6749949992"))

# 🗄 نام فایل دیتابیس
DATABASE_NAME = "mafioso.db"

# 🌐 تنظیمات پورت برای وب‌سرور Pinger روی Render
PORT = int(os.environ.get("PORT", 8080))