import base64
import os
from typing import Literal

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}

VOICE_ID_ENV = {
    "en": "VOICE_ID_EN",
    "si": "VOICE_ID_SI",
    "ta": "VOICE_ID_TA",
}

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
BEYOND_PRESENCE_STREAM_URL = "https://api.beyondpresence.ai/v1/stream"


class AvatarRequest(BaseModel):
    text: str
    language: str = "en"


class AvatarSuccessResponse(BaseModel):
    stream_url: str
    audio_generated: Literal[True] = True


class AvatarErrorResponse(BaseModel):
    stream_url: None = None
    audio_generated: Literal[False] = False
    error: str


def _voice_id_for_language(language: str) -> str:
    lang = language if language in VOICE_ID_ENV else "en"
    env_key = VOICE_ID_ENV[lang]
    voice_id = os.getenv(env_key)
    if not voice_id:
        raise ValueError(f"{env_key} is not set")
    return voice_id


async def _elevenlabs_tts(client: httpx.AsyncClient, text: str, voice_id: str) -> bytes:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY is not set")

    response = await client.post(
        ELEVENLABS_TTS_URL.format(voice_id=voice_id),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.content


async def _beyond_presence_stream(
    client: httpx.AsyncClient,
    audio_base64: str,
) -> str:
    api_key = os.getenv("BEYOND_PRESENCE_API_KEY")
    persona_id = os.getenv("BP_PERSONA_ID")
    if not api_key:
        raise ValueError("BEYOND_PRESENCE_API_KEY is not set")
    if not persona_id:
        raise ValueError("BP_PERSONA_ID is not set")

    response = await client.post(
        BEYOND_PRESENCE_STREAM_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "persona_id": persona_id,
            "audio": audio_base64,
            "format": "webrtc",
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    stream_url = data.get("stream_url") or data.get("url")
    if not stream_url:
        raise ValueError("Beyond Presence response missing stream_url")
    return stream_url


@router.options("")
async def avatar_options():
    return JSONResponse(content={}, headers=CORS_HEADERS)


@router.post("/tts")
async def avatar_tts_preview(request: AvatarRequest):
    """Return ElevenLabs audio only (for testing voice without Beyond Presence)."""
    try:
        voice_id = _voice_id_for_language(request.language)
        async with httpx.AsyncClient() as client:
            audio_bytes = await _elevenlabs_tts(client, request.text, voice_id)
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
        return JSONResponse(
            content={
                "audio_generated": True,
                "audio_base64": audio_base64,
                "content_type": "audio/mpeg",
            },
            headers=CORS_HEADERS,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"audio_generated": False, "error": str(exc)},
            headers=CORS_HEADERS,
        )


@router.post("")
async def avatar_endpoint(request: AvatarRequest):
    try:
        voice_id = _voice_id_for_language(request.language)

        async with httpx.AsyncClient() as client:
            audio_bytes = await _elevenlabs_tts(client, request.text, voice_id)
            audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
            stream_url = await _beyond_presence_stream(client, audio_base64)

        response = AvatarSuccessResponse(stream_url=stream_url)
        return JSONResponse(content=response.model_dump(), headers=CORS_HEADERS)
    except Exception as exc:
        error_response = AvatarErrorResponse(error=str(exc))
        return JSONResponse(content=error_response.model_dump(), headers=CORS_HEADERS)
