"""
Text-to-Speech vía OpenAI TTS.

Genera audio a partir de texto para:
- Voice notes del agente humano (desde el inbox, con el botón 🔊)
- (Futuro) Voice notes automáticas del agente IA closer

Modelos:
- tts-1:     rápido, ~$15/1M caracteres
- tts-1-hd:  alta calidad, ~$30/1M caracteres

Voces disponibles:
- alloy   (neutra)
- echo    (masculina suave)
- fable   (británica, masculina)
- onyx    (masculina grave, español)
- nova    (femenina natural, español) ← DEFAULT para LATAM
- shimmer (femenina cálida)

Output formats: mp3, opus, aac, flac, wav
Para WhatsApp voice notes usamos OGG (convertido desde mp3 con ffmpeg cuando haga falta).
"""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_VOICE = "nova"       # femenina natural — LATAM friendly
DEFAULT_MODEL = "tts-1"      # balance calidad/costo


def is_enabled() -> bool:
    from config.settings import get_settings
    return bool(get_settings().openai_api_key)


async def synthesize_to_bytes(
    text: str,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    output_format: str = "mp3",
) -> Optional[bytes]:
    """Genera bytes de audio desde texto. Retorna None si falla."""
    if not text or not text.strip():
        return None

    from openai import AsyncOpenAI
    from config.settings import get_settings
    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        async with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text[:4000],  # OpenAI límite 4096 chars
            response_format=output_format,
        ) as resp:
            buf = bytearray()
            async for chunk in resp.iter_bytes():
                buf.extend(chunk)
            audio_bytes = bytes(buf)
        logger.info("tts_ok", chars=len(text), bytes=len(audio_bytes), voice=voice, format=output_format)
        return audio_bytes
    except Exception as e:
        logger.error("tts_failed", error=str(e), voice=voice)
        return None


async def synthesize_to_r2(
    text: str,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    output_format: str = "mp3",
) -> Optional[dict]:
    """Genera audio y lo sube a R2. Retorna {url, filename, mime, size} o None."""
    import uuid as uuid_mod
    audio = await synthesize_to_bytes(text, voice=voice, model=model, output_format=output_format)
    if not audio:
        return None

    from integrations import storage
    ext = {"mp3": ".mp3", "opus": ".ogg", "aac": ".aac", "flac": ".flac", "wav": ".wav"}.get(output_format, ".mp3")
    mime = {"mp3": "audio/mpeg", "opus": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac", "wav": "audio/wav"}.get(output_format, "audio/mpeg")
    filename = f"tts_{uuid_mod.uuid4().hex[:12]}{ext}"
    key = f"tts/{filename}"

    if storage.is_enabled():
        url = await storage.upload_bytes(key, audio, mime)
    else:
        from pathlib import Path
        media_dir = Path(__file__).parent.parent / "media" / "tts"
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / filename).write_bytes(audio)
        url = f"media/tts/{filename}"

    return {"url": url, "filename": filename, "mime": mime, "size": len(audio)}
