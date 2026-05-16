import io
import os

from openai import AsyncOpenAI

WHISPER_MODEL = "whisper-1"

# OpenAI Audio API only accepts a subset of ISO codes.
# si (Sinhala) is NOT supported as a `language` param — use auto-detect instead.
WHISPER_API_LANG = {
    "en": "en",
    "ta": "ta",
    "zh": "zh",
    "fr": "fr",
    "de": "de",
    "es": "es",
    "hi": "hi",
}


def _client() -> AsyncOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=api_key)


async def transcribe_audio(audio_bytes: bytes, language: str = "auto") -> str:
    """Transcribe recorded speech using OpenAI Whisper."""
    buffer = io.BytesIO(audio_bytes)
    buffer.name = "audio.webm"

    kwargs: dict = {"model": WHISPER_MODEL, "file": buffer}

    # Sinhala (si): omit language — API returns 400 for language=si; model still transcribes SI audio.
    api_lang = WHISPER_API_LANG.get(language)
    if api_lang:
        kwargs["language"] = api_lang

    transcript = await _client().audio.transcriptions.create(**kwargs)
    text = (transcript.text or "").strip()
    if not text:
        raise ValueError("Could not transcribe audio — try again")
    return text
