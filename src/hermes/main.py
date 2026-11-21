#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import logging
import uvicorn
from .config import load_config
from .db.models import init_db
from .db.queries import log_message, get_conversation_history
from .router import Router
from .telegram_bot import build_app
from .dashboard.app import app as dashboard_app, configure as configure_dashboard


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
    configure_dashboard(cfg.database.path)

    router = Router(cfg)

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
                    cal_resp = type("R", (), {
                        "text": cal_reply, "provider": "calendar", "model": "calendar",
                        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": None,
                    })()
                    await log_message(cfg.database.path, text, cal_resp,
                                      platform=platform, user_id=user_id)
                    return cal_reply

        history = await get_conversation_history(
            cfg.database.path, platform, user_id, limit=cfg.router.history_limit
        )
        response = await router.route(text, history, user_id=user_id)
        await log_message(cfg.database.path, text, response,
                          platform=platform, user_id=user_id)
        return response.text

    tasks = []

    # Telegram (always required)
    tg_app = build_app(
        bot_token=cfg.telegram.bot_token,
        owner_id=cfg.telegram.owner_user_id,
        router=router,
        db_path=cfg.database.path,
    )
    tasks.append(tg_app.run_polling(drop_pending_updates=True))

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

    # Slack (optional -- webhook via FastAPI)
    if cfg.slack:
        from .slack_bot import configure as sl_cfg, build_app as sl_build
        sl_cfg(handle_message)
        result = sl_build(cfg.slack.bot_token, cfg.slack.signing_secret)
        if result[0]:
            sl_app, sl_handler = result
            from fastapi import Request
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
    await asyncio.gather(*tasks)


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
