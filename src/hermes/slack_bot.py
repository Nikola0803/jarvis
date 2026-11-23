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


def _wire_events(app, brain) -> None:
    """Attach app_mention + DM handlers that route to `brain(user_id, text, channel)`."""
    import re

    async def _run(event, say):
        text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
        user_id = event.get("user", "unknown")
        channel = event.get("channel")
        if not text:
            return
        try:
            reply = await brain(user_id, text, channel)
        except Exception as exc:  # noqa: BLE001
            log.warning("slack handler error: %s", exc)
            reply = "Something went wrong handling that."
        await say(reply or "…")

    @app.event("app_mention")
    async def handle_mention(event, say):
        await _run(event, say)

    @app.event("message")
    async def handle_dm(event, say):
        if event.get("channel_type") != "im" or event.get("bot_id"):
            return
        await _run(event, say)


def build_socket_handler(bot_token: str, app_token: str, brain):
    """Socket Mode handler for one Slack app, routing to `brain(user_id, text,
    channel)`. No public URL or signing secret needed. Returns an
    AsyncSocketModeHandler whose .start_async() runs the connection, or None."""
    try:
        from slack_bolt.async_app import AsyncApp
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    except ImportError:
        log.error("slack_bolt (with socket-mode extras) not installed -- Slack disabled")
        return None
    if not app_token:
        log.error("Slack Socket Mode needs an app-level token (xapp-...); none configured")
        return None
    app = AsyncApp(token=bot_token)
    _wire_events(app, brain)
    return AsyncSocketModeHandler(app, app_token)
