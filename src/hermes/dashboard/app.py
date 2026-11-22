from __future__ import annotations
import os
import re
import time
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from ..db.queries import get_recent_messages, get_monthly_spend

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Hermes Dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


def _take_sentences(buf: str, force: int = 180) -> tuple[list[str], str]:
    """Split off finished sentences so TTS can start on the first one.

    Returns (sentences_ready, remainder). A sentence ends on . ! ? … followed by
    whitespace, or after `force` chars at the last word boundary (long clauses).
    """
    ready: list[str] = []
    while True:
        match = _SENTENCE_END.search(buf)
        if match:
            cut = match.end()
            seg, buf = buf[:cut].strip(), buf[cut:].lstrip()
            if seg:
                ready.append(seg)
            continue
        if len(buf) >= force:
            cut = buf.rfind(" ", 0, force)
            cut = cut if cut > 0 else force
            seg, buf = buf[:cut].strip(), buf[cut:].lstrip()
            if seg:
                ready.append(seg)
            continue
        return ready, buf


_db_path: str = ""
_cfg = None
_router = None


def configure(db_path: str, cfg=None, router=None) -> None:
    global _db_path, _cfg, _router
    _db_path = db_path
    _cfg = cfg
    _router = router


async def _brain(text: str, session_id: str | None = "zero-hud") -> tuple[str, str | None]:
    """Answer `text`. Uses Hermes Agent (real harness + tools) when configured,
    otherwise falls back to the local LLM router. Returns (reply, error)."""
    ha = getattr(_cfg, "hermes_agent", None) if _cfg else None
    if ha and getattr(ha, "enabled", False) and ha.api_key:
        from ..hermes_client import ask_hermes
        return await ask_hermes(ha.base_url, ha.api_key, text,
                                model=ha.model, timeout=ha.timeout_seconds,
                                session_id=session_id)
    if _router is None:
        return "", "router not available"
    resp = await _router.route(text, [], max_tokens=80)
    return resp.text, resp.error


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


@app.get("/api/reminders")
async def api_reminders():
    """Open reminders/tasks for the HUD command-center panel."""
    from ..db.queries import list_reminders
    try:
        return {"reminders": await list_reminders(_db_path, include_done=False, limit=20)}
    except Exception as exc:  # noqa: BLE001
        return {"reminders": [], "error": str(exc)}


@app.get("/api/projects")
async def api_projects():
    """GitHub repos + open-work counts for the HUD PROJECTS panel."""
    gh_cfg = getattr(_cfg, "github", None) if _cfg else None
    if not gh_cfg or not gh_cfg.token:
        return {"projects": [], "error": "GitHub not configured."}
    from ..integrations import github as gh
    try:
        return {"projects": await gh.projects_overview(gh_cfg)}
    except Exception as exc:  # noqa: BLE001
        return {"projects": [], "error": str(exc)}


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
    Used by Z.E.R.O HUD and wake word script.

    Latency-optimised path: the reply is streamed from Hermes (reasoning disabled)
    and speech starts on the first finished sentence, so the HUD hears audio
    instead of waiting for the full reply + full TTS buffer. Every segment is one
    continuous MPEG stream (ID3 tags stripped from later segments).
    """
    import logging
    log = logging.getLogger("hermes.voice")
    from ..voice import elevenlabs_provider

    if _cfg and hasattr(_cfg, "dashboard"):
        secret = getattr(_cfg.dashboard, "secret_key", "")
        if secret and secret != "change-me":
            if request.headers.get("x-jarvis-token", "") != secret:
                log.warning("voice: rejected unauthorized request")
                return Response(status_code=401, content='{"error":"unauthorized"}',
                                media_type="application/json")

    if not elevenlabs_provider.is_ready():
        log.warning("voice: ElevenLabs not configured")
        return {"error": "Voice pipeline not configured"}

    t_start = time.monotonic()
    audio_bytes = await audio.read()
    filename = audio.filename or "voice.webm"
    mime = audio.content_type or "audio/webm"
    log.info("voice: received %d bytes (%s)", len(audio_bytes), mime)

    try:
        text = await elevenlabs_provider.transcribe(audio_bytes, filename, mime)
    except Exception as exc:
        log.error("voice: STT failed: %s", exc)
        return {"error": f"STT failed: {exc}"}

    t_stt = time.monotonic() - t_start
    log.info("voice: STT => %r (%.2fs)", text, t_stt)
    if not text:
        return {"error": "empty transcription"}

    def _h(s: str) -> str:
        return s.encode("ascii", errors="replace").decode("ascii")

    ha = getattr(_cfg, "hermes_agent", None) if _cfg else None
    if not (ha and getattr(ha, "enabled", False) and ha.api_key):
        # Fallback: local router, fully buffered (no streaming available).
        reply_text, brain_err = await _brain(text)
        log.info("voice: brain => %r (error=%s)", reply_text[:120], brain_err)
        if not reply_text:
            return {"transcript": text, "reply": "I had a brain freeze -- check the logs.",
                    "error": f"LLM empty: {brain_err}"}
        try:
            audio_out = await elevenlabs_provider.synthesize(reply_text)
        except Exception as exc:
            log.error("voice: TTS failed: %s", exc)
            return {"transcript": text, "reply": reply_text, "error": f"TTS failed: {exc}"}
        return Response(content=audio_out, media_type="audio/mpeg",
                        headers={"X-Transcript": _h(text[:200]), "X-Reply": _h(reply_text[:200])})

    from ..hermes_client import stream_hermes

    async def sentences():
        buf = ""
        async for delta in stream_hermes(ha.base_url, ha.api_key, text,
                                         model=ha.model, timeout=ha.timeout_seconds,
                                         session_id="zero-hud"):
            buf += delta
            ready, buf = _take_sentences(buf)
            for s in ready:
                yield s
        tail = buf.strip()
        if tail:
            yield tail

    gen = sentences()
    try:
        first_sentence = await gen.__anext__()
    except StopAsyncIteration:
        log.error("voice: brain returned empty text")
        return {"transcript": text, "reply": "I had a brain freeze -- check the logs.",
                "error": "LLM empty"}
    except Exception as exc:
        log.error("voice: brain stream failed: %s", exc)
        return {"transcript": text, "reply": "I had a brain freeze -- check the logs.",
                "error": f"LLM error: {exc}"}

    t_first = time.monotonic() - t_start
    log.info("voice: first sentence after %.2fs (stt %.2fs) => %r", t_first, t_stt, first_sentence[:100])
    # Rollback switch: JARVIS_SENTENCE_STREAM=0 synthesizes the whole reply in one
    # TTS call instead of per-sentence stitching.
    sentence_stream = os.environ.get("JARVIS_SENTENCE_STREAM", "1") != "0"

    async def audio_body():
        started = time.monotonic()
        first_chunk = True
        prev = None
        if not sentence_stream:
            full = first_sentence
            try:
                async for sentence in gen:
                    full = f"{full} {sentence}"
            except Exception as exc:  # noqa: BLE001
                log.warning("voice: reply stream ended early: %s", exc)
            async for chunk in elevenlabs_provider.synthesize_stream(full):
                yield chunk
            log.info("voice: single-shot TTS finished in %.2fs", time.monotonic() - started)
            return
        try:
            async for chunk in elevenlabs_provider.synthesize_stream(first_sentence):
                if first_chunk:
                    log.info("voice: first audio chunk after %.2fs total", time.monotonic() - t_start)
                    first_chunk = False
                yield chunk
        except Exception as exc:
            log.error("voice: TTS failed on first sentence: %s", exc)
            return
        prev = first_sentence
        try:
            async for sentence in gen:
                async for chunk in elevenlabs_provider.synthesize_stream(
                        sentence, previous_text=prev, strip_id3=True):
                    yield chunk
                prev = sentence
        except Exception as exc:  # noqa: BLE001 -- keep the audio already sent
            log.warning("voice: reply stream ended early: %s", exc)
        log.info("voice: stream finished in %.2fs (total %.2fs)",
                 time.monotonic() - started, time.monotonic() - t_start)

    return StreamingResponse(
        audio_body(),
        media_type="audio/mpeg",
        headers={
            "X-Transcript": _h(text[:200]),
            "X-Reply": _h(first_sentence[:200]),
        },
    )


@app.post("/api/chat")
async def api_chat(request: Request):
    """Text input endpoint: JSON {text} -> LLM -> streaming TTS out (or JSON fallback)."""
    import logging
    log = logging.getLogger("hermes.chat")

    if _cfg and hasattr(_cfg, "dashboard"):
        secret = getattr(_cfg.dashboard, "secret_key", "")
        if secret and secret != "change-me":
            if request.headers.get("x-jarvis-token", "") != secret:
                return Response(status_code=401, content='{"error":"unauthorized"}',
                                media_type="application/json")

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content='{"error":"bad request"}',
                        media_type="application/json")

    text = (body.get("text") or "").strip()
    if not text:
        return {"error": "empty text"}

    from ..voice import elevenlabs_provider
    reply_text, brain_err = await _brain(text)
    log.info("chat: %r => %r", text[:60], reply_text[:80])

    if not reply_text:
        return {"error": f"LLM empty: {brain_err}"}

    def _h(s: str) -> str:
        return s.encode("ascii", errors="replace").decode("ascii")

    # If ElevenLabs is not configured, return JSON so the HUD can show the text reply
    if not elevenlabs_provider.is_ready():
        log.info("chat: TTS not ready, returning text reply")
        import json
        return Response(
            content=json.dumps({"reply": reply_text}),
            media_type="application/json",
            headers={"X-Reply": _h(reply_text[:200])},
        )

    # Collect TTS audio; fall back to JSON on failure
    try:
        audio_bytes = await elevenlabs_provider.synthesize(reply_text)
    except Exception as exc:
        log.error("chat: TTS failed: %s", exc)
        import json
        return Response(
            content=json.dumps({"reply": reply_text, "error": f"TTS failed: {exc}"}),
            media_type="application/json",
            headers={"X-Reply": _h(reply_text[:200])},
        )

    if not audio_bytes:
        log.warning("chat: TTS returned empty audio, sending text reply")
        import json
        return Response(
            content=json.dumps({"reply": reply_text, "error": "TTS returned empty audio"}),
            media_type="application/json",
            headers={"X-Reply": _h(reply_text[:200])},
        )

    log.info("chat: returning %d bytes of audio", len(audio_bytes))
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "X-Transcript": _h(text[:200]),
            "X-Reply": _h(reply_text[:200]),
        },
    )


@app.get("/api/emails")
async def api_emails(request: Request, account: str = "all", limit: int = 10, unread_only: bool = True):
    """Read emails from configured Gmail accounts."""
    import logging
    log = logging.getLogger("hermes.emails")

    if _cfg and hasattr(_cfg, "dashboard"):
        secret = getattr(_cfg.dashboard, "secret_key", "")
        if secret and secret not in ("change-me", "your-secret-here"):
            if request.headers.get("x-jarvis-token", "") != secret:
                return Response(status_code=401, content='{"error":"unauthorized"}',
                                media_type="application/json")

    if not _cfg or not getattr(_cfg, "gmail", None):
        return {"error": "Gmail not configured. Add gmail section to config.yaml."}

    from ..integrations.gmail import list_messages
    gmail_cfg = _cfg.gmail
    accounts = gmail_cfg.accounts or []
    if account != "all":
        accounts = [a for a in accounts if a.name == account]

    result = {}
    for acct in accounts:
        try:
            msgs = await list_messages(
                acct.token_file, gmail_cfg.credentials_file,
                account_name=acct.name, unread_only=unread_only, max_results=limit,
            )
            result[acct.name] = [
                {
                    "id": m["id"],
                    "from": m["from"],
                    "subject": m["subject"],
                    "date": str(m["date"]) if m.get("date") else None,
                    "snippet": m["snippet"][:200],
                    "unread": m["unread"],
                    "priority": acct.priority,
                }
                for m in msgs
            ]
        except RuntimeError:
            result[acct.name] = {"error": "not_authenticated", "account": acct.name}
        except Exception as exc:
            log.error("emails %s: %s", acct.name, exc)
            result[acct.name] = {"error": str(exc)}

    return result


@app.get("/api/calendar")
async def api_calendar(request: Request, hours: int = 24):
    """Get upcoming calendar events."""
    if _cfg and hasattr(_cfg, "dashboard"):
        secret = getattr(_cfg.dashboard, "secret_key", "")
        if secret and secret not in ("change-me", "your-secret-here"):
            if request.headers.get("x-jarvis-token", "") != secret:
                return Response(status_code=401, content='{"error":"unauthorized"}',
                                media_type="application/json")

    if not _cfg or not _cfg.calendar:
        return {"error": "Calendar not configured."}

    from ..integrations.google_calendar import list_upcoming
    cal_cfg = _cfg.calendar
    result = {}
    for acct in (cal_cfg.accounts or []):
        try:
            events = await list_upcoming(
                acct.token_file, cal_cfg.credentials_file,
                hours=hours, user_tz=cal_cfg.timezone, account_name=acct.name,
            )
            result[acct.name] = [
                {
                    "id": e["id"],
                    "title": e["title"],
                    "start": str(e["start"]) if e.get("start") else None,
                    "location": e.get("location", ""),
                    "link": e.get("link", ""),
                }
                for e in events
            ]
        except RuntimeError:
            result[acct.name] = {"error": "not_authenticated"}
        except Exception as exc:
            result[acct.name] = {"error": str(exc)}

    return result


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
