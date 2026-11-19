"""Discord bot using discord.py (asyncio-native)."""
from __future__ import annotations
import logging
from typing import Optional, Callable, Awaitable

log = logging.getLogger("hermes.discord")

_handler: Optional[Callable] = None


def configure(message_handler: Callable) -> None:
    global _handler
    _handler = message_handler


def build_client(bot_token: str, owner_id: Optional[int] = None):
    try:
        import discord
    except ImportError:
        log.error("discord.py not installed -- Discord bot disabled")
        return None

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info("Discord bot connected as %s", client.user)

    @client.event
    async def on_message(message: discord.Message):
        if message.author == client.user:
            return
        # DMs only, or if explicitly mentioned in a server
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = client.user in message.mentions
        if not (is_dm or is_mention):
            return

        text = message.content
        if is_mention:
            text = text.replace(f"<@{client.user.id}>", "").strip()
        if not text:
            return

        user_id = str(message.author.id)
        async with message.channel.typing():
            if _handler:
                reply = await _handler("discord", user_id, text)
            else:
                reply = "Jarvis is not fully initialized."

        # Discord has a 2000-char limit per message
        while reply:
            chunk, reply = reply[:2000], reply[2000:]
            await message.reply(chunk)

    return client, bot_token
