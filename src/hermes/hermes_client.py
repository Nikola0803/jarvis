"""Client for Nous Research's Hermes Agent via its OpenAI-compatible API server.

Hermes Agent (the real agentic harness) exposes POST /v1/chat/completions on a
loopback port (default 8642) guarded by API_SERVER_KEY. This lets the Z.E.R.O
HUD's existing voice/text pipeline keep its STT + TTS while swapping the brain
from the old local router to Hermes -- which has the MCP tools (Gmail, calendar,
memory), skills and multi-step tool-calling.

Latency notes (voice path):
  * ``reasoning: false`` sends ``model_options.reasoning.enabled = false`` so a
    thinking model (deepseek-flash) skips its 1-10s reasoning block before the
    first spoken word. The API server honours it per request.
  * ``stream_hermes`` yields text deltas so TTS can start on the first sentence
    instead of waiting for the whole reply.

The reasoning-disable + streaming design in this file was proposed by ZERO
(Hermes Agent) editing its own bridge; reconciled here with the session headers.
"""
from __future__ import annotations

import json
import logging
import httpx

log = logging.getLogger("hermes.agent_client")

# Keep spoken replies short and clean -- the HUD reads them aloud via TTS.
VOICE_SYSTEM = (
    "You are Z.E.R.O, the owner's personal AI assistant, answering through a "
    "voice HUD. Reply in at most 1-3 short spoken sentences. No markdown, no "
    "bullet lists, no code blocks, no emojis. When the question needs real data "
    "(email, calendar, facts about the owner), use your tools, then answer plainly. "
    "Answer immediately; no preamble about what you are about to do."
)


# Slack/text channels: allow a normal written reply (still clean, no rambling).
TEXT_SYSTEM = (
    "You are Z.E.R.O, the owner's personal AI assistant and orchestrator, "
    "answering in Slack. Be concise and useful: a few sentences, short lists when "
    "they help. When the question needs real data (email, calendar, reminders, "
    "projects, code), use your tools, then answer plainly. No preamble about what "
    "you are about to do."
)


def _payload(text: str, *, model: str, stream: bool, session_id: str | None,
             reasoning: bool, system: str | None = None) -> dict:
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or VOICE_SYSTEM},
            {"role": "user", "content": text},
        ],
    }
    if stream:
        payload["stream"] = True
    if not reasoning:
        payload["model_options"] = {"reasoning": {"enabled": False}}
    if session_id:
        # Belt-and-suspenders continuity hints; the real mechanism is the
        # X-Hermes-Session-* headers (see _headers).
        payload["user"] = session_id
    return payload


def _headers(api_key: str, session_id: str | None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if session_id:
        # X-Hermes-Session-Id continues the conversation thread (Hermes loads the
        # prior turns from its own store); X-Hermes-Session-Key scopes long-term
        # memory per channel. Both authenticated by the Bearer key above.
        headers["X-Hermes-Session-Id"] = session_id
        headers["X-Hermes-Session-Key"] = session_id
    return headers


async def ask_hermes(
    base_url: str,
    api_key: str,
    text: str,
    *,
    model: str = "hermes",
    timeout: float = 90.0,
    session_id: str | None = None,
    reasoning: bool = False,
    system: str | None = None,
) -> tuple[str, str | None]:
    """Send one message to Hermes Agent, return (reply_text, error_or_None)."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = _headers(api_key, session_id)
    payload = _payload(text, model=model, stream=False, session_id=session_id,
                       reasoning=reasoning, system=system)
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


async def stream_hermes(
    base_url: str,
    api_key: str,
    text: str,
    *,
    model: str = "hermes",
    timeout: float = 90.0,
    session_id: str | None = None,
    reasoning: bool = False,
):
    """Async generator of reply text deltas (SSE passthrough from the API server)."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = _headers(api_key, session_id)
    payload = _payload(text, model=model, stream=True, session_id=session_id,
                       reasoning=reasoning)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread())[:200]
                    raise RuntimeError(f"hermes {r.status_code}: {body!r}")
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        raise RuntimeError(str(event["error"])[:200])
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if content:
                            yield content
    except Exception as exc:  # noqa: BLE001
        log.warning("stream_hermes error: %s", exc)
        raise
