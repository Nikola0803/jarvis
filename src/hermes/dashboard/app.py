from __future__ import annotations
import os
import time
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from ..db.queries import get_recent_messages, get_monthly_spend

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Hermes Dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_db_path: str = ""
_cfg = None
_router = None


def configure(db_path: str, cfg=None, router=None) -> None:
    global _db_path, _cfg, _router
    _db_path = db_path
    _cfg = cfg
    _router = router


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    messages = await get_recent_messages(_db_path, limit=50)
    spend = await get_monthly_spend(_db_path)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "messages": messages, "spend": spend},
    )


@app.get("/api/messages")
async def api_messages():
    return await get_recent_messages(_db_path, limit=100)


@app.get("/api/spend")
async def api_spend():
    return await get_monthly_spend(_db_path)


@app.get("/api/status")
async def api_status():
    return {"status": "running", "db": _db_path}


@app.get("/api/connections")
async def api_connections():
    """Live connection status for every integration Jarvis has."""
    conns = {}
    if _cfg:
        conns["telegram"] = {"enabled": bool(_cfg.telegram), "label": "Telegram"}
        conns["claude"] = {"enabled": bool(_cfg.anthropic), "label": "Claude"}
        conns["deepseek"] = {"enabled": bool(_cfg.deepseek), "label": "DeepSeek"}
        conns["gemini"] = {"enabled": bool(_cfg.gemini), "label": "Gemini"}
        conns["elevenlabs"] = {"enabled": bool(_cfg.elevenlabs), "label": "ElevenLabs Voice"}
        conns["calendar"] = {"enabled": bool(_cfg.calendar), "label": "Google Calendar"}
    return conns


@app.post("/api/voice")
async def api_voice(request: Request, audio: UploadFile = File(...)):
    """Voice endpoint: audio in -> STT -> LLM -> TTS -> MP3 out.
    Used by Z.E.R.O HUD and wake word script."""
    import logging
    log = logging.getLogger("hermes.voice")
    from ..voice import elevenlabs_provider

    # Token guard: if a non-default secret_key is set, require matching header
    if _cfg and hasattr(_cfg, "dashboard"):
        secret = getattr(_cfg.dashboard, "secret_key", "")
        if secret and secret != "change-me":
            provided = request.headers.get("x-jarvis-token", "")
            if provided != secret:
                log.warning("voice: rejected request with bad token")
                return Response(status_code=401, content='{"error":"unauthorized"}',
                                media_type="application/json")

    if not elevenlabs_provider.is_ready():
        log.warning("voice: ElevenLabs not configured")
        return {"error": "Voice pipeline not configured"}

    audio_bytes = await audio.read()
    filename = audio.filename or "voice.webm"
    mime = audio.content_type or "audio/webm"
    log.info("voice: received %d bytes (%s)", len(audio_bytes), mime)

    try:
        text = await elevenlabs_provider.transcribe(audio_bytes, filename, mime)
    except Exception as exc:
        log.error("voice: STT failed: %s", exc)
        return {"error": f"STT failed: {exc}"}

    log.info("voice: STT => %r", text)
    if not text:
        return {"error": "empty transcription"}

    if _router is None:
        return {"transcript": text, "error": "router not available"}  # type: ignore

    response = await _router.route(text, [], max_tokens=80)
    log.info("voice: LLM => %r (error=%s)", response.text[:120], response.error)

    if not response.text:
        log.error("voice: LLM returned empty text, error=%s", response.error)
        return {"transcript": text, "reply": "I had a brain freeze -- check the logs.", "error": f"LLM empty: {response.error}"}

    try:
        mp3_bytes = await elevenlabs_provider.synthesize(response.text)
        log.info("voice: TTS => %d bytes", len(mp3_bytes))
        def _h(s: str) -> str:
            return s.encode("ascii", errors="replace").decode("ascii")
        return Response(content=mp3_bytes, media_type="audio/mpeg",
                        headers={"X-Transcript": _h(text[:200]), "X-Reply": _h(response.text[:200])})
    except Exception as exc:
        log.error("voice: TTS failed: %s", exc)
        return {"transcript": text, "reply": response.text, "error": f"TTS failed: {exc}"}


@app.get("/api/live")
async def api_live():
    messages = await get_recent_messages(_db_path, limit=50)
    spend = await get_monthly_spend(_db_path)

    messages_today = 0
    last_ago = "No messages yet"
    now = datetime.now(timezone.utc)
    for m in messages:
        ts_raw = m.get("created_at", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if ts.date() == now.date():
                messages_today += 1
            if last_ago == "No messages yet":
                diff = int((now - ts).total_seconds())
                if diff < 60:
                    last_ago = f"{diff}s ago"
                elif diff < 3600:
                    last_ago = f"{diff // 60}m ago"
                elif diff < 86400:
                    last_ago = f"{diff // 3600}h ago"
                else:
                    last_ago = f"{diff // 86400}d ago"
        except Exception:
            pass

    active_model = "unknown"
    if messages:
        active_model = messages[0].get("model") or messages[0].get("provider") or "unknown"

    uptime_hours = 0.0
    proc_start = os.environ.get("JARVIS_START_TIME")
    if proc_start:
        try:
            uptime_hours = (time.time() - float(proc_start)) / 3600
        except Exception:
            pass

    return {
        "status": "running",
        "uptime_hours": round(uptime_hours, 1),
        "active_model": active_model,
        "messages_today": messages_today,
        "last_message_ago": last_ago,
        "spend_month_usd": round(sum(spend.values()), 4),
    }
