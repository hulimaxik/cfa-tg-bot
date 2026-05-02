#!/usr/bin/env python3
"""CFA Ops Bot — polling mode, прямое подключение к Postgres.

При старте бот делает self-test и шлёт результат владельцу.
Если что-то сломано — точный текст ошибки прилетит в TG, не нужно гадать.
"""
import os
import logging
import asyncio
import traceback
from datetime import datetime, timezone

import asyncpg
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------- ENV ----------
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_OWNER_CHAT_ID = int(os.environ["TG_OWNER_CHAT_ID"])
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "Не задана DATABASE_URL (или SUPABASE_DB_URL). "
        "Возьми Connection String → Session mode из Supabase → Settings → Database."
    )

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("cfa-bot")


# ---------- DB POOL ----------
class DB:
    """Простой singleton-пул соединений к Postgres."""
    pool: asyncpg.Pool | None = None

    @classmethod
    async def connect(cls) -> asyncpg.Pool:
        if cls.pool is None:
            # statement_cache_size=0 — обязательно для Supabase pooler (pgbouncer)
            cls.pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=1,
                max_size=3,
                statement_cache_size=0,
                command_timeout=15,
            )
        return cls.pool

    @classmethod
    async def fetch(cls, q: str, *args):
        pool = await cls.connect()
        async with pool.acquire() as c:
            return await c.fetch(q, *args)

    @classmethod
    async def fetchrow(cls, q: str, *args):
        pool = await cls.connect()
        async with pool.acquire() as c:
            return await c.fetchrow(q, *args)

    @classmethod
    async def execute(cls, q: str, *args):
        pool = await cls.connect()
        async with pool.acquire() as c:
            return await c.execute(q, *args)


# ---------- AUTH ----------
def is_owner(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == TG_OWNER_CHAT_ID)


def deny_silently(update: Update) -> bool:
    """True если не owner — ничего не отвечаем (не палимся)."""
    return not is_owner(update)


# ---------- HELPERS ----------
def short(s: str, n: int = 200) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."


def mask(v: str | None) -> str:
    """Маскирует секрет: 'apify_xxxx12345' -> 'apify_xxx...12345'"""
    if not v:
        return "❌ not set"
    if len(v) <= 16:
        return "(set, short)"
    return v[:8] + "..." + v[-6:]


# ---------- HANDLERS ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if deny_silently(update):
        return
    text = (
        "🤖 *CFA Ops Bot*\n\n"
        "Команды:\n"
        "`/diag` — полная диагностика (ENV + DB + таблицы)\n"
        "`/status` — посты и расход за сегодня\n"
        "`/budget` — расход в разрезе сервисов\n"
        "`/sources` — список источников\n"
        "`/posts` — последние 5 собранных постов\n"
        "`/logs` — последние ошибки\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_diag(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if deny_silently(update):
        return
    await update.message.reply_text("🔍 Диагностика...")

    parts: list[str] = []

    # 1. ENV
    env_keys = [
        "TG_BOT_TOKEN", "TG_OWNER_CHAT_ID",
        "DATABASE_URL", "SUPABASE_DB_URL",
        "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
    ]
    env_lines = [f"`{k}`: {mask(os.environ.get(k))}" for k in env_keys]
    parts.append("*ENV:*\n" + "\n".join(env_lines))

    # 2. DB ping
    try:
        row = await DB.fetchrow(
            "SELECT current_database() AS db, "
            "now() AT TIME ZONE 'Europe/Moscow' AS msk, version() AS v"
        )
        parts.append(
            f"*DB:* ✅\n"
            f"DB: `{row['db']}`\n"
            f"MSK: `{row['msk']}`\n"
            f"PG: `{short(row['v'], 60)}`"
        )
    except Exception as e:
        parts.append(
            f"*DB:* ❌\n"
            f"`{type(e).__name__}: {short(e, 250)}`"
        )

    # 3. Tables
    try:
        rows = await DB.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name"
        )
        names = [r["table_name"] for r in rows]
        parts.append(f"*Таблиц в public:* {len(names)}\n" + ", ".join(f"`{n}`" for n in names))
    except Exception as e:
        parts.append(f"*Tables:* ❌ `{short(e, 200)}`")

    # 4. Sources
    try:
        n = await DB.fetchrow(
            "SELECT count(*) c, count(*) FILTER (WHERE is_active) act FROM sources"
        )
        parts.append(f"*Sources:* {n['c']} (активных: {n['act']})")
    except Exception as e:
        parts.append(f"*Sources:* ❌ `{short(e, 150)}`")

    # 5. Posts last 24h
    try:
        n = await DB.fetchrow(
            "SELECT count(*) c FROM raw_posts "
            "WHERE collected_at > now() - interval '24 hours'"
        )
        parts.append(f"*Постов за 24ч:* {n['c']}")
    except Exception as e:
        parts.append(f"*Posts:* ❌ `{short(e, 150)}`")

    # 6. Cost today
    try:
        n = await DB.fetchrow(
            "SELECT coalesce(sum(cost_usd),0) s FROM cost_tracking "
            "WHERE created_at >= current_date"
        )
        parts.append(f"*Расход сегодня:* ${float(n['s']):.4f}")
    except Exception as e:
        parts.append(f"*Cost:* ❌ `{short(e, 150)}`")

    text = "\n\n".join(parts)
    if len(text) > 3900:
        text = text[:3900] + "..."
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if deny_silently(update):
        return
    try:
        srcs = await DB.fetchrow(
            "SELECT count(*) c, count(*) FILTER (WHERE is_active) a FROM sources"
        )
        posts = await DB.fetchrow(
            "SELECT count(*) c FROM raw_posts WHERE collected_at >= current_date"
        )
        cost = await DB.fetchrow(
            "SELECT coalesce(sum(cost_usd),0) s FROM cost_tracking WHERE created_at >= current_date"
        )
        text = (
            "📊 *Status*\n"
            f"Sources: {srcs['c']} (active: {srcs['a']})\n"
            f"Posts today: {posts['c']}\n"
            f"Cost today: ${float(cost['s']):.4f}"
        )
    except Exception as e:
        text = f"❌ DB error\n`{type(e).__name__}: {short(e, 250)}`"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if deny_silently(update):
        return
    try:
        rows = await DB.fetch(
            "SELECT service, sum(cost_usd) total, count(*) n "
            "FROM cost_tracking WHERE created_at >= current_date "
            "GROUP BY service ORDER BY total DESC"
        )
        if not rows:
            text = "💰 Расход сегодня: $0.00 (нет данных)"
        else:
            lines = ["💰 *Бюджет сегодня:*"]
            total = 0.0
            for r in rows:
                t = float(r["total"])
                total += t
                lines.append(f"  {r['service']}: ${t:.4f} ({r['n']} ops)")
            lines.append(f"\n*Итого:* ${total:.4f} / лимит $5.00")
            text = "\n".join(lines)
    except Exception as e:
        text = f"❌ `{short(e, 250)}`"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_sources(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if deny_silently(update):
        return
    try:
        rows = await DB.fetch(
            "SELECT name, platform, is_active FROM sources ORDER BY platform, name"
        )
        if not rows:
            text = "Нет источников"
        else:
            lines = []
            for r in rows:
                mark = "✅" if r["is_active"] else "⏸️"
                lines.append(f"{mark} `{r['platform']:<10}` {r['name']}")
            text = "*Sources:*\n" + "\n".join(lines)
    except Exception as e:
        text = f"❌ `{short(e, 250)}`"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_posts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if deny_silently(update):
        return
    try:
        rows = await DB.fetch(
            "SELECT platform, author, "
            "       coalesce(title, left(content, 80), '(no text)') AS preview, "
            "       collected_at "
            "FROM raw_posts ORDER BY collected_at DESC LIMIT 5"
        )
        if not rows:
            text = "Нет собранных постов"
        else:
            lines = ["📥 *Последние 5 постов:*"]
            for r in rows:
                ts = r["collected_at"].strftime("%m-%d %H:%M")
                author = r["author"] or "?"
                lines.append(f"`{ts}` [{r['platform']}] @{author}")
                lines.append(f"  _{short(r['preview'], 80)}_")
            text = "\n".join(lines)
    except Exception as e:
        text = f"❌ `{short(e, 250)}`"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if deny_silently(update):
        return
    try:
        rows = await DB.fetch(
            "SELECT created_at, level, module, message FROM system_logs "
            "WHERE level IN ('ERROR','CRITICAL','WARN') "
            "ORDER BY created_at DESC LIMIT 5"
        )
        if not rows:
            text = "✅ Нет ошибок"
        else:
            lines = ["⚠️ *Последние ошибки:*"]
            for r in rows:
                ts = r["created_at"].strftime("%m-%d %H:%M")
                lines.append(
                    f"`{ts}` [{r['level']}] {r['module']}: {short(r['message'], 80)}"
                )
            text = "\n".join(lines)
    except Exception as e:
        text = f"❌ `{short(e, 250)}`"
    await update.message.reply_text(text, parse_mode="Markdown")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Handler error: %s", ctx.error, exc_info=ctx.error)
    try:
        if isinstance(update, Update) and update.effective_user \
                and update.effective_user.id == TG_OWNER_CHAT_ID:
            await update.message.reply_text(
                f"❌ Internal error: `{type(ctx.error).__name__}: {short(ctx.error, 200)}`",
                parse_mode="Markdown",
            )
    except Exception:
        pass


# ---------- STARTUP SELF-TEST ----------
async def post_init(app: Application):
    """Бот при старте проверяет всё и шлёт результат владельцу."""
    parts = ["🤖 *Bot starting up*"]

    # DB
    try:
        row = await DB.fetchrow("SELECT current_database() AS db, version() AS v")
        parts.append(f"DB: ✅ `{row['db']}`")
    except Exception as e:
        parts.append(
            f"DB: ❌ `{type(e).__name__}`\n"
            f"`{short(e, 200)}`\n\n"
            f"*Что проверить:*\n"
            f"• `DATABASE_URL` (или `SUPABASE_DB_URL`) в Railway Variables\n"
            f"• Формат: `postgresql://postgres.<ref>:<pass>@<host>:5432/postgres`\n"
            f"• Хост в Supabase → Settings → Database → Connection String → *Session mode*"
        )
        text = "\n\n".join(parts)
        try:
            await app.bot.send_message(
                chat_id=TG_OWNER_CHAT_ID, text=text, parse_mode="Markdown"
            )
        except Exception as ex:
            log.error("Failed to send startup error: %s", ex)
        return

    # Tables existence
    try:
        rows = await DB.fetch(
            "SELECT count(*) c FROM information_schema.tables "
            "WHERE table_schema='public'"
        )
        parts.append(f"Tables: {rows[0]['c']}")
    except Exception as e:
        parts.append(f"Tables: ❌ `{short(e, 100)}`")

    # Quick stats
    try:
        srcs = await DB.fetchrow("SELECT count(*) c FROM sources")
        posts = await DB.fetchrow("SELECT count(*) c FROM raw_posts")
        parts.append(f"Sources: {srcs['c']}, Posts: {posts['c']}")
    except Exception as e:
        parts.append(f"Stats: ❌ `{short(e, 100)}`")

    parts.append("✅ Готов. Жми `/diag` или `/status`")
    text = "\n".join(parts)

    try:
        await app.bot.send_message(
            chat_id=TG_OWNER_CHAT_ID, text=text, parse_mode="Markdown"
        )
    except Exception as e:
        log.error("Failed to send startup message: %s", e)


def main():
    log.info("Building application...")
    app = (
        Application.builder()
        .token(TG_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("diag", cmd_diag))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("posts", cmd_posts))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_error_handler(on_error)

    log.info("Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
