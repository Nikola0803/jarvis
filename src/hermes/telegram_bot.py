from __future__ import annotations
import io
import logging
from functools import wraps
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from .router import Router
from .db.queries import log_message, get_monthly_spend

log = logging.getLogger(__name__)


def owner_only(owner_id: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if update.effective_user.id != owner_id:
                log.warning("Blocked unauthorized user %d", update.effective_user.id)
                return
            return await func(update, ctx)
        return wrapper
    return decorator


def build_app(
    bot_token: str,
    owner_id: int,
    router: Router,
    db_path: str,
    elevenlabs_api_key: str | None = None,
    elevenlabs_voice_id: str | None = None,
    anthropic_api_key: str | None = None,
    anthropic_model: str = "claude-sonnet-4-5",
) -> Application:
    if elevenlabs_api_key:
        from .voice import elevenlabs_provider
        elevenlabs_provider.init(
            api_key=elevenlabs_api_key,
            voice_id=elevenlabs_voice_id or elevenlabs_provider.DEFAULT_VOICE_ID,
        )

    app = Application.builder().token(bot_token).build()

    @owner_only(owner_id)
    async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        text = update.message.text or ""
        await update.message.chat.send_action("typing")

        history = ctx.chat_data.get("history", [])
        response = await router.route(text, history)

        if response.error and not response.text:
            await update.message.reply_text("Something went wrong. Try again.")
            return

        reply = response.text
        await update.message.reply_text(reply)

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        ctx.chat_data["history"] = history[-20:]

        await log_message(db_path, text, response)

    @owner_only(owner_id)
    async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from .voice import elevenlabs_provider
        if not elevenlabs_provider.is_ready():
            await update.message.reply_text("Voice pipeline not configured.")
            return

        await update.message.chat.send_action("typing")

        # Download OGG voice file from Telegram
        tg_file = await ctx.bot.get_file(update.message.voice.file_id)
        audio_bytes = bytes(await tg_file.download_as_bytearray())

        # STT via Scribe v3
        try:
            text = await elevenlabs_provider.transcribe(audio_bytes)
        except Exception as exc:
            log.error("STT error: %s", exc)
            await update.message.reply_text("Couldn't transcribe that. Try again.")
            return

        if not text:
            await update.message.reply_text("Didn't catch anything.")
            return

        log.info("Voice STT: %r", text[:80])

        # Route through LLM
        history = ctx.chat_data.get("history", [])
        response = await router.route(text, history)

        if response.error and not response.text:
            await update.message.reply_text("Something went wrong. Try again.")
            return

        reply = response.text

        # TTS via v4 Turbo, send as audio
        try:
            audio_data = await elevenlabs_provider.synthesize(reply)
            await update.message.reply_audio(
                audio=io.BytesIO(audio_data),
                filename="jarvis.mp3",
                caption=f"_{text}_",
                parse_mode="Markdown",
            )
        except Exception as exc:
            log.error("TTS error: %s", exc)
            # Fallback to text if voice fails
            await update.message.reply_text(f"_{text}_\n\n{reply}", parse_mode="Markdown")

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        ctx.chat_data["history"] = history[-20:]

        await log_message(db_path, text, response)

    @owner_only(owner_id)
    async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from .voice import elevenlabs_provider
        spend = await get_monthly_spend(db_path)
        lines = ["*Hermes Status*", "Agent: running"]
        for provider, cost in spend.items():
            lines.append(f"{provider}: ${cost:.4f} this month")
        if not spend:
            lines.append("No spend recorded yet.")
        voice_status = "enabled" if elevenlabs_provider.is_ready() else "disabled"
        lines.append(f"Voice: {voice_status}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    @owner_only(owner_id)
    async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        ctx.chat_data["history"] = []
        await update.message.reply_text("Conversation history cleared.")

    @owner_only(owner_id)
    async def cmd_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not anthropic_api_key:
            await update.message.reply_text("Anthropic API key not configured.")
            return
        args = ctx.args
        if not args:
            await update.message.reply_text(
                "Usage: /update <instruction>\nExample: /update make responses shorter"
            )
            return
        instruction = " ".join(args)
        await update.message.chat.send_action("typing")
        await update.message.reply_text("Processing self-improvement request...")
        from .self_improve import apply as _improve
        status = await _improve(instruction, anthropic_api_key, anthropic_model)
        await update.message.reply_text(status)

    @owner_only(owner_id)
    async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        improve_line = "\n/update <instruction> - self-improve (Jarvis edits its own code)" if anthropic_api_key else ""
        msg = (
            "*Hermes Commands*\n"
            "/status - show monthly spend + voice status\n"
            "/reset - clear conversation history\n"
            f"/help - this message{improve_line}\n\n"
            "*Trigger prefixes (force Claude):*\n"
            "/code, /analyze, /plan, /think, /research\n\n"
            "*Voice:* send a voice message to get a spoken reply"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    return app
