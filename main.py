import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Header
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
import httpx

# ─── CONFIG ───
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET", "default-secret-change-me")
TG_OWNER_CHAT_ID = int(os.getenv("TG_OWNER_CHAT_ID", "0"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cfa-bot")

# ─── SUPABASE HELPERS ───
async def get_state(key: str):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/system_state?key=eq.{key}&select=value",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        )
        data = r.json()
        return data[0]["value"] if data else None

async def set_state(key: str, value: str):
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/system_state?key=eq.{key}",
            json={"value": value, "updated_by": "tg-bot"},
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            }
        )
        return r.status_code in (200, 204)

# ─── TG GUARD ───
def owner_only(handler):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id if update.effective_chat else 0
        if cid != TG_OWNER_CHAT_ID:
            log.warning(f"Denied access from chat_id={cid}")
            return
        return await handler(update, ctx)
    return wrapper

# ─── HANDLERS ───
@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 CFA Ops Bot запущен.\n"
        "Команды:\n"
        "/status — статус системы\n"
        "/stop — аварийный стоп\n"
        "/resume — снять стоп"
    )

@owner_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        stop = await get_state("emergency_stop")
        cb = await get_state("circuit_breaker_tripped")
        health = await get_state("last_health_check")
        text = (
            f"📊 *Статус системы*\n\n"
            f"🛑 Emergency stop: `{stop}`\n"
            f"⚡ Circuit breaker: `{cb}`\n"
            f"💓 Last health: `{health}`"
        )
    except Exception as e:
        text = f"⚠️ Ошибка чтения БД: {e}"
    await update.message.reply_text(text, parse_mode="Markdown")

@owner_only
async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Остановить", callback_data="stop:yes"),
         InlineKeyboardButton("❌ Отмена", callback_data="stop:no")]
    ])
    await update.message.reply_text("🛑 Остановить ВСЁ?", reply_markup=kb)

@owner_only
async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Запустить", callback_data="resume:yes"),
         InlineKeyboardButton("❌ Отмена", callback_data="resume:no")]
    ])
    await update.message.reply_text("▶️ Снять стоп?", reply_markup=kb)

@owner_only
async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, decision = q.data.split(":")
    if decision != "yes":
        await q.edit_message_text("Отмена.")
        return
    if action == "stop":
        ok = await set_state("emergency_stop", "true")
        await q.edit_message_text("🛑 Стоп активирован." if ok else "⚠️ Ошибка.")
    elif action == "resume":
        ok1 = await set_state("emergency_stop", "false")
        ok2 = await set_state("circuit_breaker_tripped", "false")
        await q.edit_message_text("▶️ Система запущена." if (ok1 and ok2) else "⚠️ Частичная ошибка.")

async def cmd_unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pass  # молчаливый игнор

# ─── PTB APP ───
ptb_app = (
    Application.builder()
    .token(TG_BOT_TOKEN)
    .updater(None)
    .build()
)
ptb_app.add_handler(CommandHandler("start", cmd_start))
ptb_app.add_handler(CommandHandler("status", cmd_status))
ptb_app.add_handler(CommandHandler("stop", cmd_stop))
ptb_app.add_handler(CommandHandler("resume", cmd_resume))
ptb_app.add_handler(CallbackQueryHandler(cb_confirm, pattern=r"^(stop|resume):(yes|no)$"))
ptb_app.add_handler(MessageHandler(filters.ALL, cmd_unknown))

# ─── FASTAPI ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    await ptb_app.initialize()
    await ptb_app.bot.set_webhook(
        url=f"https://{os.getenv('RAILWAY_PUBLIC_DOMAIN', 'localhost')}/tg/{TG_WEBHOOK_SECRET}",
        secret_token=TG_WEBHOOK_SECRET,
    )
    await ptb_app.start()
    log.info("Bot started")
    yield
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.post("/tg/{secret}")
async def webhook(secret: str, request: Request,
                   x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if secret != TG_WEBHOOK_SECRET or x_telegram_bot_api_secret_token != TG_WEBHOOK_SECRET:
        raise HTTPException(403)
    data = await request.json()
    await ptb_app.process_update(Update.de_json(data, ptb_app.bot))
    return {"ok": True}

@app.post("/n8n/notify")
async def n8n_notify(request: Request):
    # Заглушка для будущих алертов от n8n
    payload = await request.json()
    text = payload.get("text", "Alert")
    await ptb_app.bot.send_message(chat_id=TG_OWNER_CHAT_ID, text=text, parse_mode="Markdown")
    return {"ok": True}
