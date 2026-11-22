#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import logging
import uvicorn
from fastapi import Request
from .config import load_config
from .db.models import init_db
from .db.queries import log_message, get_conversation_history
from .router import Router
from .llm.base import LLMResponse
from .telegram_bot import build_app
from .dashboard.app import app as dashboard_app, configure as configure_dashboard
from .mcp_server import configure as configure_mcp, build_mcp_router
from .agents import AgentScheduler, DawnAgent, MiloAgent, get_store as get_approval_store
from .memory import get_store as get_memory_store


def _stub_response(text: str, provider: str) -> LLMResponse:
    """Wrap a non-LLM reply (calendar/gmail) so it logs with the right columns."""
    return LLMResponse(text=text, provider=provider, model=provider,
                       input_tokens=0, output_tokens=0, cost_usd=0.0, error=None)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def main():
    cfg = load_config()
    setup_logging(cfg.server.get("log_level", "INFO"))
    log = logging.getLogger("hermes")

    log.info("Initializing database at %s", cfg.database.path)
    await init_db(cfg.database.path)

    # Init memory store (must come before Router so context injection works)
    memory = get_memory_store(cfg.database.path)
    log.info("Memory store ready")

    # Seed owner name from config if not already set
    if not memory.get_profile("name"):
        memory.set_profile("name", "Nikola")
    if not memory.get_profile("timezone"):
        tz = cfg.calendar.timezone if cfg.calendar else "Europe/Belgrade"
        memory.set_profile("timezone", tz)

    # Init Slack notifier if configured
    if cfg.slack:
        from .integrations.slack_notifier import init as init_slack
        notifier = init_slack(cfg.slack.bot_token)
        log.info("Slack notifier ready")

    router = Router(cfg)
    configure_dashboard(cfg.database.path, cfg, router)
    configure_mcp(cfg, router, cfg.database.path)

    # Mount MCP server if the mcp package is installed
    mcp_router = build_mcp_router()
    if mcp_router:
        dashboard_app.include_router(mcp_router)
        log.info("MCP server mounted at /mcp/sse + /mcp/messages/")

    async def handle_message(platform: str, user_id: str, text: str) -> str:
        """Unified handler called by every platform adapter."""
        # Calendar / timezone intent intercept (before LLM)
        if cfg.calendar:
            from .integrations.calendar_commands import is_calendar_intent, handle as cal_handle
            if is_calendar_intent(text):
                cal_reply = await cal_handle(
                    text,
                    cfg.calendar,
                    user_tz=cfg.calendar.timezone,
                )
                if cal_reply is not None:
                    await log_message(cfg.database.path, text, _stub_response(cal_reply, "calendar"),
                                      platform=platform, user_id=user_id)
                    return cal_reply

        # Gmail intent intercept (before LLM)
        if cfg.gmail:
            from .integrations.gmail_commands import is_mail_intent, handle as mail_handle
            if is_mail_intent(text):
                mail_reply = await mail_handle(text, cfg.gmail)
                if mail_reply is not None:
                    await log_message(cfg.database.path, text, _stub_response(mail_reply, "gmail"),
                                      platform=platform, user_id=user_id)
                    return mail_reply

        history = await get_conversation_history(
            cfg.database.path, platform, user_id, limit=cfg.router.history_limit
        )
        response = await router.route(text, history, user_id=user_id)
        await log_message(cfg.database.path, text, response,
                          platform=platform, user_id=user_id)
        return response.text

    # Build approval store (shared between agents and Telegram bot)
    approval_store = get_approval_store()
    # scheduler_ref is populated after Telegram app is built -- avoids chicken-and-egg
    scheduler_ref: list = [None]

    tasks = []

    # Telegram (always required)
    tg_app = build_app(
        bot_token=cfg.telegram.bot_token,
        owner_id=cfg.telegram.owner_user_id,
        router=router,
        db_path=cfg.database.path,
        elevenlabs_api_key=cfg.elevenlabs.api_key if cfg.elevenlabs else None,
        elevenlabs_voice_id=cfg.elevenlabs.voice_id if cfg.elevenlabs else None,
        anthropic_api_key=cfg.anthropic.api_key if cfg.anthropic else None,
        anthropic_model=cfg.anthropic.model if cfg.anthropic else "claude-sonnet-4-5",
        gmail_cfg=cfg.gmail if cfg.gmail else None,
        calendar_cfg=cfg.calendar if cfg.calendar else None,
        approval_store=approval_store,
        scheduler_ref=scheduler_ref,
    )
    await tg_app.initialize()
    await tg_app.start()
    # Drop pending updates and use a generous timeout to avoid 409 Conflict when
    # the previous process's long-poll connection is still alive on Telegram's side.
    await tg_app.updater.start_polling(
        drop_pending_updates=True,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30,
        pool_timeout=30,
        allowed_updates=["message", "callback_query"],
    )
    log.info("Telegram bot polling started")

    # Agent scheduler -- Dawn (8am briefing) + Milo (8am + 4pm email triage)
    user_tz = getattr(cfg, "timezone", None) or (cfg.calendar.timezone if cfg.calendar else "Europe/Belgrade")
    scheduler = AgentScheduler(user_tz=user_tz)
    for AgentClass in (DawnAgent, MiloAgent):
        scheduler.register(AgentClass(
            cfg=cfg,
            bot=tg_app,
            approval_store=approval_store,
            db_path=cfg.database.path,
        ))
    scheduler_ref[0] = scheduler  # wire into the /agent command handler
    scheduler.start()
    log.info("Agent scheduler started (%s timezone)", user_tz)

    # WhatsApp (optional -- webhook via FastAPI)
    if cfg.whatsapp:
        from .whatsapp_bot import configure as wa_cfg, router as wa_router
        wa_cfg(cfg.whatsapp, handle_message)
        dashboard_app.include_router(wa_router)
        log.info("WhatsApp webhook at /webhook/whatsapp")

    # Discord (optional)
    if cfg.discord:
        from .discord_bot import configure as dc_cfg, build_client
        dc_cfg(handle_message)
        result = build_client(cfg.discord.bot_token, cfg.discord.owner_id)
        if result:
            dc_client, dc_token = result
            tasks.append(dc_client.start(dc_token))
            log.info("Discord bot starting")

    # Slack (optional). Socket Mode when an app-level token is set (no public URL
    # needed); otherwise the legacy HTTP webhook. Slack talks to the Hermes Agent
    # brain (full tools) when enabled, falling back to the local router.
    if cfg.slack:
        from .slack_bot import configure as sl_cfg

        async def slack_brain(platform: str, user_id: str, text: str) -> str:
            ha = getattr(cfg, "hermes_agent", None)
            if ha and ha.enabled:
                from .hermes_client import ask_hermes, TEXT_SYSTEM
                reply, err = await ask_hermes(
                    ha.base_url, ha.api_key, text,
                    model=ha.model, timeout=ha.timeout_seconds,
                    session_id=f"slack:{user_id}", reasoning=False,
                    system=TEXT_SYSTEM,
                )
                if reply:
                    return reply
                if err:
                    log.warning("slack hermes error: %s", err)
            return await handle_message(platform, user_id, text)

        sl_cfg(slack_brain)
        if getattr(cfg.slack, "app_token", ""):
            from .slack_bot import build_socket_handler
            sl_handler = build_socket_handler(cfg.slack.bot_token, cfg.slack.app_token)
            if sl_handler:
                tasks.append(sl_handler.start_async())
                log.info("Slack Socket Mode started")
        else:
            from .slack_bot import build_app as sl_build
            result = sl_build(cfg.slack.bot_token, cfg.slack.signing_secret)
            if result[0]:
                sl_app, sl_handler = result
                @dashboard_app.post("/webhook/slack")
                async def slack_events(req: Request):
                    return await sl_handler.handle(req)
                log.info("Slack webhook at /webhook/slack")

    # FastAPI (dashboard + all webhooks)
    uv_config = uvicorn.Config(
        dashboard_app,
        host=cfg.dashboard.host,
        port=cfg.dashboard.port,
        log_level="warning",
    )
    uv_server = uvicorn.Server(uv_config)
    tasks.append(uv_server.serve())

    log.info(
        "Jarvis starting: Telegram + dashboard on %s:%d, %d extra platform(s)",
        cfg.dashboard.host, cfg.dashboard.port,
        sum(1 for x in [cfg.whatsapp, cfg.discord, cfg.slack] if x),
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
