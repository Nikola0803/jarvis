"""Slack bot using slack_bolt async."""
from __future__ import annotations
import logging
from typing import Optional, Callable

log = logging.getLogger("hermes.slack")

_handler: Optional[Callable] = None


def configure(message_handler: Callable) -> None:
    global _handler
    _handler = message_handler


def build_app(bot_token: str, signing_secret: str):
    try:
        from slack_bolt.async_app import AsyncApp
        from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
    except ImportError:
        log.error("slack_bolt not installed -- Slack bot disabled")
        return None, None

    app = AsyncApp(token=bot_token, signing_secret=signing_secret)

    @app.event("app_mention")
    async def handle_mention(event, say):
        text = event.get("text", "")
        # strip the bot mention token
        import re
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
        user_id = event.get("user", "unknown")
        if _handler and text:
            reply = await _handler("slack", user_id, text)
            await say(reply)

    @app.event("message")
    async def handle_dm(event, say):
        # only handle DMs (channel_type = "im"), ignore public channels
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id"):
            return
        text = event.get("text", "").strip()
        user_id = event.get("user", "unknown")
        if _handler and text:
            reply = await _handler("slack", user_id, text)
            await say(reply)

    handler = AsyncSlackRequestHandler(app)
    return app, handler
