from __future__ import annotations
import asyncio
import io
import logging
import threading
from elevenlabs import ElevenLabs

log = logging.getLogger(__name__)

SCRIBE_MODEL = "scribe_v3"
TTS_MODEL = "eleven_turbo_v2_5"
# Rachel -- clear American female, works well for an AI assistant
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

_client: ElevenLabs | None = None
_voice_id: str = DEFAULT_VOICE_ID


def init(api_key: str, voice_id: str = DEFAULT_VOICE_ID) -> None:
    global _client, _voice_id
    _client = ElevenLabs(api_key=api_key)
    _voice_id = voice_id
    log.info("ElevenLabs client initialised (voice=%s)", voice_id)


def is_ready() -> bool:
    return _client is not None


def _transcribe_sync(audio_bytes: bytes, filename: str, mime: str) -> str:
    assert _client is not None
    result = _client.speech_to_text.convert(
        file=(filename, io.BytesIO(audio_bytes), mime),
        model_id=SCRIBE_MODEL,
    )
    return (result.text or "").strip()


def _synthesize_sync(text: str) -> bytes:
    assert _client is not None
    chunks = _client.text_to_speech.convert(
        text=text,
        voice_id=_voice_id,
        model_id=TTS_MODEL,
        output_format="mp3_44100_128",
    )
    return b"".join(chunks)


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg", mime: str = "audio/ogg") -> str:
    if _client is None:
        raise RuntimeError("ElevenLabs not initialised -- missing API key")
    return await asyncio.to_thread(_transcribe_sync, audio_bytes, filename, mime)


async def synthesize(text: str) -> bytes:
    if _client is None:
        raise RuntimeError("ElevenLabs not initialised -- missing API key")
    return await asyncio.to_thread(_synthesize_sync, text)


async def synthesize_stream(text: str):
    """Async generator: yields MP3 chunks as they arrive from ElevenLabs (streaming TTS)."""
    if _client is None:
        raise RuntimeError("ElevenLabs not initialised -- missing API key")
    loop = asyncio.get_event_loop()
    q: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _produce():
        try:
            for chunk in _client.text_to_speech.convert(
                text=text,
                voice_id=_voice_id,
                model_id=TTS_MODEL,
                output_format="mp3_44100_128",
            ):
                if chunk:
                    loop.call_soon_threadsafe(q.put_nowait, chunk)
        except Exception as exc:
            log.error("TTS stream error: %s", exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=_produce, daemon=True).start()
    while True:
        chunk = await q.get()
        if chunk is None:
            break
        yield chunk
