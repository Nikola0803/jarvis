"""Client for Nous Research's Hermes Agent via its OpenAI-compatible API server.

Hermes Agent (the real agentic harness) exposes POST /v1/chat/completions on a
loopback port (default 8642) guarded by API_SERVER_KEY. This lets the Z.E.R.O
HUD's existing voice/text pipeline keep its STT + TTS while swapping the brain
from the old local router to Hermes -- which has the MCP tools (Gmail, calendar,
memory), skills and multi-step tool-calling.
"""
from __future__ import annotations

import logging
import httpx

log = logging.getLogger("hermes.agent_client")

# Keep spoken replies short and clean -- the HUD reads them aloud via TTS.
VOICE_SYSTEM = (
    "You are Z.E.R.O, the owner's personal AI assistant, answering through a "
    "voice HUD. Reply in at most 1-3 short spoken sentences. No markdown, no "
    "bullet lists, no code blocks, no emojis. When the question needs real data "
    "(email, calendar, facts about the owner), use your tools, then answer plainly."
)


async def ask_hermes(
    base_url: str,
    api_key: str,
    text: str,
    *,
    model: str = "hermes",
    timeout: float = 90.0,
    session_id: str | None = None,
) -> tuple[str, str | None]:
    """Send one message to Hermes Agent, return (reply_text, error_or_None)."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": VOICE_SYSTEM},
            {"role": "user", "content": text},
        ],
    }
    if session_id:
        # Real conversation continuity: Hermes loads this session's prior turns
        # from its own store (X-Hermes-Session-Id) and scopes long-term memory to
        # the channel (X-Hermes-Session-Key). Both are authenticated by the Bearer
        # key above. We still send only the latest user turn; history lives server-side.
        headers["X-Hermes-Session-Id"] = session_id
        headers["X-Hermes-Session-Key"] = session_id
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            return "", f"hermes {r.status_code}: {r.text[:200]}"
        data = r.json()
        reply = (data["choices"][0]["message"]["content"] or "").strip()
        return reply, None
    except Exception as exc:  # noqa: BLE001 -- never crash the HUD request
        log.warning("ask_hermes error: %s", exc)
        return "", str(exc)
