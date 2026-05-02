#!/usr/bin/env python3
"""
CFA Ops Bot — polling mode
"""
import os
import logging
import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    filters,
)
from supabase import create_client, Client

# ─── CONFIG ───
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_OWNER_CHAT_ID = int(os.environ["TG_OWNER_CHAT_ID"])
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# ─── LOGGING ───
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── SUPABASE ───
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ─── HELPERS ───
def is_owner(update: Update) -> bool:
    return update.effective_user.id == TG_OWNER_CHAT_ID

async def log_command(update: Update, command: str, response: str = ""):
    try:
        supabase.table("tg_commands").insert({
            "chat_id": update.effective_chat.id,
            "username": update.effective_user.username,
            "command": command,
            "response": response,
        }).execute()
    except Exception as e:
        logger.error(f"log_command error: {e}")

# ─── HANDLERS ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    text = (
        "🤖 <b>CFA Ops Bot запущен</b>
"
        "Режим: polling
"
        f"Время: {datetime.utcnow().strftime('%H:%M UTC')}

"
        "Команды:
"
        "/status — статус системы
"
        "/budget — расходы сегодня
"
        "/stop — остановить (требует подтверждения)
"
        "/logs — последние ошибки"
    )
    await update.message.reply_html(text)
    await log_command(update, "start")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    try:
        sources = supabase.table("sources").select("*", count="exact").execute()
        src_count = sources.count or 0

        today = datetime.utcnow().strftime("%Y-%m-%d")
        posts = supabase.table("raw_posts").select("*", count="exact").gte("collected_at", today).execute()
        post_count = posts.count or 0

        costs = supabase.table("cost_tracking").select("cost_usd").gte("created_at", today).execute()
        total_cost = sum(float(r.get("cost_usd", 0)) for r in costs.data)

        text = (
            f"📊 <b>Статус CFA</b>
"
            f"Источников: {src_count}
"
            f"Постов сегодня: {post_count}
"
            f"Расход сегодня: ${total_cost:.4f}
"
            f"Бот: ✅ polling"
        )
    except Exception as e:
        text = f"⚠️ Ошибка базы: {str(e)[:100]}"
        logger.error(text)

    await update.message.reply_html(text)
    await log_command(update, "status", text)

async def budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        costs = supabase.table("cost_tracking").select("service,cost_usd").gte("created_at", today).execute()
        lines = [f"• {r['service']}: ${float(r['cost_usd']):.4f}" for r in costs.data]
        total = sum(float(r.get("cost_usd", 0)) for r in costs.data)
        text = (
            f"💰 <b>Бюджет сегодня</b>
"
            + ("
".join(lines) if lines else "Нет расходов") +
            f"

<b>Итого: ${total:.4f}</b>"
        )
    except Exception as e:
        text = f"⚠️ Ошибка: {str(e)[:100]}"
    await update.message.reply_html(text)
    await log_command(update, "budget", text)

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("⛔ Только владелец.")
        return
    await update.message.reply_text(
        "🛑 Для остановки напиши: <b>Подтверждаю</b>",
        parse_mode="HTML"
    )
    await log_command(update, "stop", "awaiting confirmation")

async def confirm_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if update.message.text.strip().lower() in ["подтверждаю", "confirm", "yes"]:
        await update.message.reply_text("🛑 Остановка... Бот выключится через 3 сек.")
        await log_command(update, "stop", "confirmed")
        await asyncio.sleep(2)
        os._exit(0)

async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    try:
        rows = supabase.table("system_logs").select("*").order("created_at", desc=True).limit(5).execute()
        lines = []
        for r in rows.data:
            t = r["created_at"][:16].replace("T", " ")
            lines.append(f"[{t}] {r['level']} | {r['module']} | {r['message'][:60]}")
        text = "📋 <b>Последние логи</b>

" + "
".join(lines) if lines else "Логов пока нет."
    except Exception as e:
        text = f"⚠️ {str(e)[:100]}"
    await update.message.reply_html(text)
    await log_command(update, "logs", text)

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update):
        await update.message.reply_text("❓ Неизвестная команда. Попробуй /status")

# ─── MAIN ───
def main():
    application = Application.builder().token(TG_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("budget", budget))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("logs", logs_cmd))
    application.add_handler(
        filters.TEXT & filters.Chat(chat_id=TG_OWNER_CHAT_ID),
        confirm_stop,
    )
    application.add_handler(
        filters.COMMAND,
        unknown,
    )

    logger.info("Starting CFA Ops Bot in POLLING mode...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
