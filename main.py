#!/usr/bin/env python3
"""CFA Ops Bot — simple polling"""
import os
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from supabase import create_client

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_OWNER_CHAT_ID = int(os.environ["TG_OWNER_CHAT_ID"])
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def is_owner(update):
    return update.effective_user.id == TG_OWNER_CHAT_ID

async def start(update, context):
    if not is_owner(update):
        await update.message.reply_text("Access denied.")
        return
    await update.message.reply_text("CFA Ops Bot started. Commands: /status /budget /stop /logs")

async def status(update, context):
    if not is_owner(update):
        return
    try:
        src = supabase.table("sources").select("*", count="exact").execute()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        posts = supabase.table("raw_posts").select("*", count="exact").gte("collected_at", today).execute()
        costs = supabase.table("cost_tracking").select("cost_usd").gte("created_at", today).execute()
        total = sum(float(r.get("cost_usd", 0)) for r in costs.data)
        text = "Status: sources=%d posts_today=%d cost=$%.4f" % (src.count or 0, posts.count or 0, total)
    except Exception as e:
        text = "DB error: " + str(e)[:100]
        logger.error(text)
    await update.message.reply_text(text)

async def budget(update, context):
    if not is_owner(update):
        return
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        costs = supabase.table("cost_tracking").select("service,cost_usd").gte("created_at", today).execute()
        total = sum(float(r.get("cost_usd", 0)) for r in costs.data)
        text = "Budget today: $%.4f" % total
    except Exception as e:
        text = "Error: " + str(e)[:100]
    await update.message.reply_text(text)

async def stop_cmd(update, context):
    if not is_owner(update):
        await update.message.reply_text("Only owner.")
        return
    await update.message.reply_text("Stopping...")
    await asyncio.sleep(1)
    os._exit(0)

async def logs_cmd(update, context):
    if not is_owner(update):
        return
    try:
        rows = supabase.table("system_logs").select("*").order("created_at", desc=True).limit(3).execute()
        lines = []
        for r in rows.data:
            lines.append(r["created_at"][:16] + " | " + r["level"] + " | " + r["message"][:50])
        text = "\n".join(lines) if lines else "No logs."
    except Exception as e:
        text = "Error: " + str(e)[:100]
    await update.message.reply_text(text)

def main():
    app = Application.builder().token(TG_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("budget", budget))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))
    logger.info("Starting bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
