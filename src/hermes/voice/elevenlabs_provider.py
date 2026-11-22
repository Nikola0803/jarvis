from __future__ import annotations
import asyncio
import io
import logging
import os
import threading
from elevenlabs import ElevenLabs

log = logging.getLogger(__name__)

# scribe_v2 is ~3x faster than v3 on short clips (0.3s vs 0.9s measured) and
# plenty accurate for spoken commands; override with JARVIS_STT_MODEL.
SCRIBE_MODEL = os.environ.get("JARVIS_STT_MODEL", "scribe_v2")
TTS_MODEL = os.environ.get("JARVIS_TTS_MODEL", "eleven_turbo_v2_5")
# Rachel -- clear American female, works well for an AI assistant
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
# Latency optimisation for chunked (sentence-by-sentence) synthesis. 3 = max
# latency optimisations while keeping text normalisation.
STREAM_LATENCY = int(os.environ.get("JARVIS_TTS_LATENCY", "3"))

_client: ElevenLabs | None = None
_voice_id: str = DEFAULT_VOICE_ID


def init(api_key: str, voice_id: str = DEFAULT_VOICE_ID) -> None:
    global _client, _voice_id
    _client = ElevenLabs(api_key=api_key)
    _voice_id = voice_id
    log.info("ElevenLabs client initialised (voice=%s)", voice_id)


def is_ready() -> bool:
    return _client is not None


def voice_id() -> str:
    return _voice_id


def _transcribe_sync(audio_bytes: bytes, filename: str, mime: str, model_id: str) -> str:
    assert _client is not None
    result = _client.speech_to_text.convert(
        file=(filename, io.BytesIO(audio_bytes), mime),
        model_id=model_id,
    )
    return (result.text or "").strip()


def _synthesize_sync(text: str, model_id: str = TTS_MODEL,
                     output_format: str = "mp3_44100_128", previous_text: str | None = None) -> bytes:
    assert _client is not None
    kwargs = {}
    if previous_text:
        kwargs["previous_text"] = previous_text
    chunks = _client.text_to_speech.convert(
        text=text,
        voice_id=_voice_id,
        model_id=model_id,
        output_format=output_format,
        optimize_streaming_latency=STREAM_LATENCY,
        **kwargs,
    )
    return b"".join(chunks)


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg",
                     mime: str = "audio/ogg", model_id: str | None = None) -> str:
    if _client is None:
        raise RuntimeError("ElevenLabs not initialised -- missing API key")
    return await asyncio.to_thread(_transcribe_sync, audio_bytes, filename, mime,
                                   model_id or SCRIBE_MODEL)


async def synthesize(text: str) -> bytes:
    if _client is None:
        raise RuntimeError("ElevenLabs not initialised -- missing API key")
    return await asyncio.to_thread(_synthesize_sync, text)


def _strip_id3(first_chunk: bytes) -> bytes:
    """Drop a leading ID3v2 tag so stitched segments stay one clean MPEG stream."""
    if not first_chunk.startswith(b"ID3") or len(first_chunk) < 10:
        return first_chunk
    size = ((first_chunk[6] & 0x7F) << 21) | ((first_chunk[7] & 0x7F) << 14) | \
           ((first_chunk[8] & 0x7F) << 7) | (first_chunk[9] & 0x7F)
    end = 10 + size
    return first_chunk[end:] if end < len(first_chunk) else b""


async def synthesize_stream(text: str, *, previous_text: str | None = None,
                            strip_id3: bool = False):
    """Async generator: yields MP3 chunks as they arrive from ElevenLabs (streaming TTS)."""
    if _client is None:
        raise RuntimeError("ElevenLabs not initialised -- missing API key")
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _produce():
        try:
            kwargs = {}
            if previous_text:
                kwargs["previous_text"] = previous_text
            for chunk in _client.text_to_speech.convert(
                text=text,
                voice_id=_voice_id,
                model_id=TTS_MODEL,
                output_format="mp3_44100_128",
                optimize_streaming_latency=STREAM_LATENCY,
                **kwargs,
            ):
                if chunk:
                    loop.call_soon_threadsafe(q.put_nowait, chunk)
        except Exception as exc:
            log.error("TTS stream error: %s", exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=_produce, daemon=True).start()
    first = True
    while True:
        chunk = await q.get()
        if chunk is None:
            break
        if first:
            first = False
            if strip_id3:
                chunk = _strip_id3(chunk)
                if not chunk:
                    continue
        yield chunk
