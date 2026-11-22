"""Slack notifier for Hermes agents.

Each agent has a channel and a persona (display name + emoji icon).
One bot token posts on behalf of all agents using per-message username overrides.

Usage:
    notifier = SlackNotifier(bot_token="xoxb-...")
    await notifier.post("Dawn", "Good morning! Here's your day...", channel="#ops")
"""
from __future__ import annotations
import logging
import aiohttp
import json

log = logging.getLogger("hermes.slack")

# Agent persona definitions
AGENT_PERSONAS: dict[str, dict] = {
    "Dawn":  {"emoji": ":sunrise:", "channel": "#dawn"},
    "Milo":  {"emoji": ":email:",   "channel": "#milo"},
    "Scout": {"emoji": ":mag:",     "channel": "#scout"},
    "Dev":   {"emoji": ":hammer:",  "channel": "#dev"},
    "Remy":  {"emoji": ":calendar:", "channel": "#remy"},
    "Hermes": {"emoji": ":zap:",    "channel": "#ops"},
}

OPS_CHANNEL = "#ops"


class SlackNotifier:
    def __init__(self, bot_token: str):
        self._token = bot_token
        self._base = "https://slack.com/api"

    async def post(
        self,
        agent_name: str,
        text: str,
        channel: str | None = None,
        also_ops: bool = True,
    ) -> bool:
        """Post a message as an agent. Also posts to #ops if also_ops=True."""
        persona = AGENT_PERSONAS.get(agent_name, {"emoji": ":robot_face:", "channel": "#ops"})
        target = channel or persona["channel"]
        username = f"{persona['emoji']} {agent_name}"

        ok = await self._send(target, text, username)

        if also_ops and target != OPS_CHANNEL:
            # Brief summary to #ops
            summary = text[:200] + ("..." if len(text) > 200 else "")
            await self._send(OPS_CHANNEL, f"*{agent_name}:* {summary}", username)

        return ok

    async def post_blocks(
        self,
        agent_name: str,
        blocks: list[dict],
        text: str = "",
        channel: str | None = None,
    ) -> bool:
        """Post rich Block Kit message."""
        persona = AGENT_PERSONAS.get(agent_name, {"emoji": ":robot_face:", "channel": "#ops"})
        target = channel or persona["channel"]
        username = f"{persona['emoji']} {agent_name}"
        return await self._send(target, text, username, blocks=blocks)

    async def _send(
        self,
        channel: str,
        text: str,
        username: str,
        blocks: list[dict] | None = None,
    ) -> bool:
        payload: dict = {
            "channel": channel,
            "text": text,
            "username": username,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if blocks:
            payload["blocks"] = blocks

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base}/chat.postMessage",
                    headers=headers,
                    data=json.dumps(payload),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        log.warning("Slack post failed: %s", data.get("error"))
                        return False
                    return True
        except Exception as exc:
            log.error("Slack send error: %s", exc)
            return False

    async def test_connection(self) -> str:
        """Call auth.test and return workspace name, or error string."""
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._base}/auth.test",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        return f"Connected to {data.get('team')} as {data.get('user')}"
                    return f"Error: {data.get('error')}"
        except Exception as exc:
            return f"Connection failed: {exc}"


# Global singleton
_notifier: SlackNotifier | None = None


def init(bot_token: str) -> SlackNotifier:
    global _notifier
    _notifier = SlackNotifier(bot_token)
    return _notifier


def get_notifier() -> SlackNotifier | None:
    return _notifier
