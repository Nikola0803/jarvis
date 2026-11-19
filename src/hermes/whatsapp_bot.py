"""WhatsApp Cloud API handler (Meta Business API, webhook-based)."""
from __future__ import annotations
import hashlib
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Request, Response

log = logging.getLogger("hermes.whatsapp")

router = APIRouter()

_cfg = None          # WhatsAppConfig, set by configure()
_handler = None      # async callable(user_id, text) -> str, set by configure()


def configure(cfg, message_handler) -> None:
    global _cfg, _handler
    _cfg = cfg
    _handler = message_handler


def _verify_sig(body: bytes, sig_header: str) -> bool:
    if not _cfg or not _cfg.app_secret:
        return True  # skip verification if no secret configured
    expected = "sha256=" + hmac.new(
        _cfg.app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header or "")


@router.get("/webhook/whatsapp")
async def wa_verify(request: Request):
    """Meta webhook verification challenge."""
    params = request.query_params
    if (params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == (_cfg.verify_token if _cfg else "")):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook/whatsapp")
async def wa_receive(request: Request):
    body = await request.body()
    sig = request.headers.get("x-hub-signature-256", "")
    if not _verify_sig(body, sig):
        log.warning("WhatsApp: bad signature")
        return Response(status_code=401)

    import json
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=400)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue
                from_id = msg["from"]
                text = msg["text"]["body"]
                if _handler:
                    reply = await _handler("whatsapp", from_id, text)
                    await _send_whatsapp(from_id, reply)

    return Response(content='{"status":"ok"}', media_type="application/json")


async def _send_whatsapp(to: str, text: str) -> None:
    if not _cfg:
        return
    import httpx
    url = f"https://graph.facebook.com/v19.0/{_cfg.phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {_cfg.access_token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code != 200:
            log.error("WhatsApp send failed: %s %s", r.status_code, r.text)
