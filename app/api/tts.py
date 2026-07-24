"""TTS and transcription routes: /tts, /transcribe."""

import logging

import edge_tts
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse

from app.core.streaming import tts_cache_key
from app.models import TTSRequest

from config import TTS_VOICE, TTS_RATE, VOICE_CACHE_DIR, GROQ_API_KEYS

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter()


@router.post("/tts")
async def text_to_speech(request: TTSRequest):
    """Standalone TTS endpoint. Checks local cache first; synthesizes + saves on miss."""
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    # Compute cache key — always defined, never conditional
    _, cache_path = tts_cache_key(text)

    # --- Cache Hit: serve the file directly from SSD ---
    if cache_path is not None and cache_path.exists():
        logger.info("[TTS-API] Cache hit  '%s'", text[:50])
        return FileResponse(
            path=str(cache_path),
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-cache"},
        )

    # --- Cache Miss: synthesize online, stream to client, save to disk ---
    logger.info("[TTS-API] Cache miss '%s' -> synthesizing online...", text[:50])

    async def generate_and_cache():
        try:
            communicate = edge_tts.Communicate(text=text, voice=TTS_VOICE, rate=TTS_RATE)
            parts = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    parts.append(chunk["data"])
                    yield chunk["data"]
            # After streaming is complete, persist to disk for future cache hits
            if parts and cache_path is not None:
                try:
                    VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(b"".join(parts))
                    logger.info("[TTS-API] Saved to cache '%s'", text[:50])
                except Exception as save_exc:
                    logger.warning("[TTS-API] Cache save failed: %s", save_exc)
        except Exception as exc:
            logger.error("[TTS-API] Synthesis error: %s", exc)

    return StreamingResponse(
        generate_and_cache(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribe audio using Groq Whisper. Fallback for Web Speech API."""
    if not GROQ_API_KEYS:
        raise HTTPException(status_code=503, detail="No Groq API keys configured")

    audio_bytes = await file.read()
    if not audio_bytes or len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio file is empty or too small")

    logger.info("[TRANSCRIBE] Received audio: %d bytes, type=%s", len(audio_bytes), file.content_type or "unknown")

    import io
    last_err = None
    for i, key in enumerate(GROQ_API_KEYS):
        try:
            from groq import Groq
            client = Groq(api_key=key)
            # Wrap bytes in a file-like tuple: (filename, file_obj, content_type)
            audio_file = ("audio.webm", io.BytesIO(audio_bytes), file.content_type or "audio/webm")
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=audio_file,
                language="en",
            )
            text = (transcription.text or "").strip()
            logger.info("[TRANSCRIBE] Success (key %d): '%s'", i, text[:100])
            return {"text": text}
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if "429" in str(e) or "rate limit" in err_str:
                logger.warning("[TRANSCRIBE] Key %d rate limited, trying next...", i)
                continue
            logger.error("[TRANSCRIBE] Key %d error: %s", i, e)
            continue

    logger.error("[TRANSCRIBE] All keys failed. Last error: %s", last_err)
    raise HTTPException(status_code=503, detail=f"Transcription failed: {last_err}")
