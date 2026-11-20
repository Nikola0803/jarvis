from __future__ import annotations
import io
import logging
from functools import wraps
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
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
    gmail_cfg=None,
    calendar_cfg=None,
    approval_store=None,
    scheduler_ref: list | None = None,
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

        # Gmail intent intercept
        if gmail_cfg:
            from .integrations.gmail_commands import is_mail_intent, handle as mail_handle
            if is_mail_intent(text):
                mail_reply = await mail_handle(text, gmail_cfg)
                if mail_reply is not None:
                    await update.message.reply_text(mail_reply)
                    return

        # Calendar intent intercept
        if calendar_cfg:
            from .integrations.calendar_commands import is_calendar_intent, handle as cal_handle
            if is_calendar_intent(text):
                cal_reply = await cal_handle(text, calendar_cfg)
                if cal_reply is not None:
                    await update.message.reply_text(cal_reply)
                    return

        history = ctx.chat_data.get("history", [])
        response = await router.route(text, history, max_tokens=300)

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

        # Route through LLM (voice -- keep short)
        history = ctx.chat_data.get("history", [])
        response = await router.route(text, history, max_tokens=80)

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
            "/status - monthly spend + voice status\n"
            "/reset - clear conversation history\n"
            "/memory - see what Hermes knows about you\n"
            "/remember key=value - store a profile fact\n"
            "/agent list - list all running agents\n"
            "/agent <name> - manually trigger an agent\n"
            f"/help - this message{improve_line}\n\n"
            "*Trigger prefixes (force Claude):*\n"
            "/code, /analyze, /plan, /think, /research\n\n"
            "*Voice:* send a voice message to get a spoken reply"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    @owner_only(owner_id)
    async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle approve/edit/skip buttons sent by agents."""
        query = update.callback_query
        await query.answer()

        if not approval_store or not query.data:
            return

        try:
            action, approval_id = query.data.split(":", 1)
        except ValueError:
            return

        approval = approval_store.get(approval_id)
        if not approval:
            await query.edit_message_reply_markup(reply_markup=None)
            await ctx.bot.send_message(chat_id=owner_id, text="Action expired or already resolved.")
            return

        approval_store.remove(approval_id)
        await query.edit_message_reply_markup(reply_markup=None)

        if action == "approve":
            if approval.on_approve:
                try:
                    result = await approval.on_approve()
                    await ctx.bot.send_message(
                        chat_id=owner_id,
                        text=f"<b>{approval.agent_name}:</b> {result or 'Done.'}",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    log.error("on_approve failed: %s", exc)
                    await ctx.bot.send_message(chat_id=owner_id, text=f"Action failed: {exc}")
            else:
                await ctx.bot.send_message(chat_id=owner_id, text="Approved.")

        elif action == "edit":
            hint = approval.on_edit or "Reply with the edited version."
            await ctx.bot.send_message(
                chat_id=owner_id,
                text=f"<b>Edit mode.</b> {hint}",
                parse_mode="HTML",
            )

        elif action == "skip":
            await ctx.bot.send_message(
                chat_id=owner_id,
                text=f"<b>{approval.agent_name}:</b> Skipped.",
                parse_mode="HTML",
            )

    @owner_only(owner_id)
    async def cmd_agent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Manually trigger an agent: /agent dawn, /agent milo, /agent list"""
        args = ctx.args
        if not args:
            await update.message.reply_text("Usage: /agent <name> or /agent list")
            return

        sub = args[0].lower()

        sched = scheduler_ref[0] if scheduler_ref else None
        if not sched:
            await update.message.reply_text("Agent scheduler not running.")
            return

        if sub == "list":
            names = [a.name for a in sched._agents]
            await update.message.reply_text(
                "Registered agents:\n" + "\n".join(f"  {n}" for n in names) if names else "No agents registered."
            )
            return

        ok = await sched.trigger(sub)
        if ok:
            await update.message.reply_text(f"Triggered agent: {sub}")
        else:
            names = [a.name for a in sched._agents]
            await update.message.reply_text(
                f"Agent not found: {sub}\nAvailable: {', '.join(names)}"
            )

    @owner_only(owner_id)
    async def cmd_remember(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/remember key=value -- store a profile fact.
        Examples:
          /remember name=Nikola
          /remember timezone=Europe/Belgrade
          /remember Ivan is my main client and always gets fast replies
        """
        args_text = " ".join(ctx.args) if ctx.args else ""
        if not args_text:
            await update.message.reply_text(
                "Usage:\n/remember key=value\n/remember Ivan is my main client"
            )
            return
        try:
            from .memory import get_store
            mem = get_store()
            if "=" in args_text and " " not in args_text.split("=")[0]:
                key, _, value = args_text.partition("=")
                mem.set_profile(key.strip(), value.strip())
                await update.message.reply_text(f"Profile updated: {key.strip()} = {value.strip()}")
            else:
                # Free-form fact -- store as "owner" subject
                parts = args_text.split(" ", 2)
                if len(parts) >= 3:
                    subject, relation, value = parts[0], parts[1], " ".join(parts[2:])
                else:
                    subject, relation, value = "owner", "note", args_text
                mem.add_fact(subject, relation, value, source="manual")
                await update.message.reply_text(f"Remembered: {subject} {relation} {value}")
        except Exception as exc:
            await update.message.reply_text(f"Error: {exc}")

    @owner_only(owner_id)
    async def cmd_memory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/memory -- show what Hermes knows about you."""
        try:
            from .memory import get_store
            mem = get_store()
            profile = mem.all_profile()
            facts = mem.recent_facts(limit=10)

            lines = ["*What Hermes knows:*\n"]
            if profile:
                lines.append("*Profile:*")
                for k, v in profile.items():
                    lines.append(f"  {k}: {v}")
            if facts:
                lines.append("\n*Recent facts:*")
                for f in facts:
                    lines.append(f"  {f['subject']} {f['relation']} {f['value']}")
            if not profile and not facts:
                lines.append("Nothing stored yet. Chat normally and I'll learn over time.")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"Error: {exc}")

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("remember", cmd_remember))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    return app
