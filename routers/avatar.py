import base64
import os
from typing import Literal

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lib.bey_presence import (
    api_base,
    create_call,
    ensure_setup,
    list_agents as bey_list_agents,
    resolve_agent_id,
)
from lib.chat_service import run_chat

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
# api.beyondpresence.ai no longer resolves — current API is https://api.bey.dev
BEY_API_BASE_DEFAULT = "https://api.bey.dev"
LEGACY_STREAM_URL_DEFAULT = "https://api.beyondpresence.ai/v1/stream"


class AvatarRequest(BaseModel):
    text: str
    language: str = "en"


class AvatarAskRequest(BaseModel):
    message: str
    language: str = "auto"
    history: list[dict] = Field(default_factory=list)
    session_id: str | None = None


class AvatarSetupRequest(BaseModel):
    """Optional body for POST /api/avatar/setup (e.g. ngrok URL from the frontend)."""
    public_base_url: str | None = Field(
        default=None,
        description="Public API URL Bey can reach (https://xxxx.ngrok-free.app). "
        "Saved for this server process if NILA_PUBLIC_BASE_URL is not in .env.",
    )


class AvatarSuccessResponse(BaseModel):
    stream_url: str
    audio_generated: Literal[True] = True
    provider: dict | None = None


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


def _bey_api_base() -> str:
    return (os.getenv("BEYOND_PRESENCE_API_BASE") or BEY_API_BASE_DEFAULT).rstrip("/")


def _bey_agent_id() -> str:
    agent_id = (os.getenv("BEY_AGENT_ID") or os.getenv("BP_PERSONA_ID") or "").strip()
    if not agent_id:
        raise ValueError(
            "BEY_AGENT_ID is not set (Beyond Presence agent id from "
            "https://app.bey.chat/myAgents — BP_PERSONA_ID is accepted as an alias)"
        )
    return agent_id


def _use_legacy_bey_stream() -> bool:
    return os.getenv("BEYOND_PRESENCE_USE_LEGACY", "").lower() in (
        "true",
        "1",
        "yes",
    )


async def _legacy_audio_stream(
    client: httpx.AsyncClient,
    api_key: str,
    audio_base64: str,
) -> tuple[str, dict]:
    """Deprecated one-shot audio upload API (host often offline)."""
    legacy_url = (
        os.getenv("BEYOND_PRESENCE_LEGACY_STREAM_URL") or LEGACY_STREAM_URL_DEFAULT
    )
    persona_id = _bey_agent_id()
    try:
        response = await client.post(
            legacy_url,
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
    except httpx.ConnectError as exc:
        raise ValueError(
            f"Cannot reach legacy Beyond Presence host ({legacy_url}). "
            "That domain (api.beyondpresence.ai) is retired. "
            "Unset BEYOND_PRESENCE_USE_LEGACY and use BEY_AGENT_ID with api.bey.dev."
        ) from exc
    response.raise_for_status()
    data = response.json()
    stream_url = data.get("stream_url") or data.get("url")
    if not stream_url:
        raise ValueError("Beyond Presence response missing stream_url")
    return stream_url, {**data, "transport": "legacy_stream"}


def _bey_http_error(response: httpx.Response, context: str) -> ValueError:
    try:
        body = response.json()
        detail = body.get("detail", body)
    except Exception:
        detail = response.text[:300]
    agent_id = _bey_agent_id() if response.status_code == 404 else None
    if response.status_code == 404:
        return ValueError(
            f"Beyond Presence agent not found (404): {agent_id!r}. "
            "Copy the agent id from https://app.bey.chat/myAgents (URL path after "
            "bey.chat/) and set BEY_AGENT_ID in .env. "
            f"Or call GET /api/avatar/agents to list agents. Detail: {detail}"
        )
    if response.status_code == 401:
        return ValueError(
            "Beyond Presence API key rejected (401). Create a key at "
            "https://app.bey.chat/settings"
        )
    return ValueError(f"{context}: HTTP {response.status_code} — {detail}")


async def _bey_list_agents(client: httpx.AsyncClient, api_key: str) -> list[dict]:
    response = await client.get(
        f"{_bey_api_base()}/v1/agents",
        headers={"x-api-key": api_key},
        timeout=30.0,
    )
    if not response.is_success:
        raise _bey_http_error(response, "Could not list Beyond Presence agents")
    data = response.json()
    if isinstance(data, list):
        return data
    return data.get("agents") or data.get("data") or []


async def _bey_create_call(client: httpx.AsyncClient, api_key: str) -> dict:
    """Current Beyond Presence API: managed agent via LiveKit."""
    agent_id = _bey_agent_id()
    response = await client.post(
        f"{_bey_api_base()}/v1/calls",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={"agent_id": agent_id},
        timeout=60.0,
    )
    if not response.is_success:
        raise _bey_http_error(response, "Could not create Beyond Presence call")
    return response.json()


async def _beyond_presence_stream(
    client: httpx.AsyncClient,
    audio_base64: str,
) -> tuple[str, dict]:
    api_key = os.getenv("BEYOND_PRESENCE_API_KEY")
    if not api_key:
        raise ValueError("BEYOND_PRESENCE_API_KEY is not set")

    if _use_legacy_bey_stream():
        return await _legacy_audio_stream(client, api_key, audio_base64)

    try:
        agent_id = await resolve_agent_id(client)
        data = await create_call(client, agent_id)
    except httpx.ConnectError as exc:
        raise ValueError(
            f"Cannot reach Beyond Presence API at {api_base()}. "
            f"Check network/DNS. ({exc})"
        ) from exc

    livekit_url = data.get("livekit_url") or ""
    livekit_token = data.get("livekit_token") or ""
    if not livekit_url or not livekit_token:
        raise ValueError(
            "Beyond Presence call response missing livekit_url/livekit_token. "
            f"Response: {data}"
        )

    provider = {
        **data,
        "livekit_url": livekit_url,
        "livekit_token": livekit_token,
        "transport": "livekit",
        "note": "Real-time speech-to-speech via Beyond Presence LiveKit.",
        "agent_id": data.get("agent_id"),
    }
    return livekit_url, provider


async def _tts_audio_base64(text: str, language: str) -> str:
    spoken = (text or "").strip()
    if not spoken:
        raise ValueError("No text to speak")
    voice_id = _voice_id_for_language(language)
    async with httpx.AsyncClient() as client:
        audio_bytes = await _elevenlabs_tts(client, spoken, voice_id)
    return base64.b64encode(audio_bytes).decode("utf-8")


async def _stream_avatar_for_text(text: str, language: str) -> dict:
    """ElevenLabs TTS → Beyond Presence video stream (optional)."""
    audio_base64 = await _tts_audio_base64(text, language)
    avatar_error: str | None = None
    stream_url: str | None = None
    provider: dict | None = None
    try:
        async with httpx.AsyncClient() as client:
            stream_url, provider = await _beyond_presence_stream(client, audio_base64)
    except Exception as exc:
        avatar_error = str(exc)
    return {
        "stream_url": stream_url,
        "audio_generated": True,
        "audio_base64": audio_base64,
        "content_type": "audio/mpeg",
        "provider": provider,
        "avatar_error": avatar_error,
        "embed_url": None,
    }


@router.get("/status")
async def avatar_status():
    """Config checklist for the avatar test UI (no secrets exposed)."""
    supabase_ok = False
    try:
        from lib.rag import count_documents

        supabase_ok = (await count_documents()) > 0
    except Exception:
        pass

    agent_set = bool(os.getenv("BEY_AGENT_ID") or os.getenv("BP_PERSONA_ID"))
    bey_key = os.getenv("BEYOND_PRESENCE_API_KEY")
    bey_ok = False
    bey_message = ""
    if bey_key and agent_set:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                verify = await client.get(
                    f"{_bey_api_base()}/v1/auth/verify",
                    headers={"x-api-key": bey_key},
                )
            bey_ok = verify.status_code == 204
            if not bey_ok:
                bey_message = f"HTTP {verify.status_code}"
        except httpx.HTTPError as exc:
            bey_message = str(exc)

    return JSONResponse(
        content={
            "elevenlabs_key": bool(os.getenv("ELEVENLABS_API_KEY")),
            "voice_id_en": bool(os.getenv("VOICE_ID_EN")),
            "voice_id_si": bool(os.getenv("VOICE_ID_SI")),
            "voice_id_ta": bool(os.getenv("VOICE_ID_TA")),
            "beyond_presence_key": bool(bey_key),
            "beyond_presence_api_ok": bey_ok,
            "beyond_presence_api_base": _bey_api_base(),
            "beyond_presence_message": bey_message,
            "agent_id": agent_set,
            "use_legacy_stream": _use_legacy_bey_stream(),
            "supabase_ok": supabase_ok,
            "openai_key": bool(os.getenv("OPENAI_API_KEY")),
            "ready": bool(
                os.getenv("ELEVENLABS_API_KEY")
                and os.getenv("VOICE_ID_EN")
                and os.getenv("BEYOND_PRESENCE_API_KEY")
                and agent_set
                and os.getenv("OPENAI_API_KEY")
                and bey_ok
            ),
        },
        headers=CORS_HEADERS,
    )


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


def _apply_public_base_url(url: str | None) -> str | None:
    normalized = (url or "").strip().rstrip("/")
    if not normalized:
        return None
    os.environ["NILA_PUBLIC_BASE_URL"] = normalized
    return normalized


@router.post("/setup")
async def avatar_setup(body: AvatarSetupRequest | None = None):
    """
    Create or resolve a Beyond Presence agent for real-time speech-to-speech.

    If BEY_AGENT_ID in .env is missing or invalid, creates a Nila agent on your account.
    Copy the returned agent_id into .env as BEY_AGENT_ID.
  Optional JSON body: { "public_base_url": "https://xxxx.ngrok-free.app" }
    """
    try:
        applied = _apply_public_base_url(body.public_base_url if body else None)
        result = await ensure_setup()
        if applied:
            result["public_base_url_applied"] = applied
        env_id = os.getenv("BEY_AGENT_ID") or os.getenv("BP_PERSONA_ID") or ""
        result["env_agent_id"] = env_id or None
        result["env_agent_valid"] = bool(env_id and env_id == result["agent_id"])
        from lib.bey_presence import public_llm_base

        if not public_llm_base():
            result["local_mode"] = True
            result["message"] = (
                "Local mode — agent ready for LiveKit. Optional: set NILA_PUBLIC_BASE_URL "
                "for spoken Supabase RAG."
            )
        elif not result.get("rag_enabled"):
            result["message"] = result.get("rag_message") or (
                "Set NILA_PUBLIC_BASE_URL (ngrok) for Supabase RAG in live speech."
            )
        elif not result["env_agent_valid"]:
            result["message"] = (
                f"Set BEY_AGENT_ID={result['agent_id']} in .env and restart the API."
            )
        else:
            result["message"] = (
                "Agent ready — use POST /api/avatar/live/ws or Connect on /avatar."
            )
        return JSONResponse(content=result, headers=CORS_HEADERS)
    except Exception as exc:
        return JSONResponse(
            content={"ok": False, "error": str(exc)},
            headers=CORS_HEADERS,
        )


@router.get("/agents")
async def list_agents():
    """List Beyond Presence agents (pick BEY_AGENT_ID from here)."""
    try:
        async with httpx.AsyncClient() as client:
            agents = await bey_list_agents(client)
    except ValueError as exc:
        return JSONResponse(
            status_code=502,
            content={"error": str(exc), "agents": []},
            headers=CORS_HEADERS,
        )
    simplified = []
    for item in agents:
        if not isinstance(item, dict):
            continue
        aid = item.get("id") or item.get("agent_id")
        simplified.append(
            {
                "id": aid,
                "name": item.get("name") or item.get("title"),
                "embed_url": f"https://bey.chat/{aid}" if aid else None,
            }
        )
    return JSONResponse(
        content={"agents": simplified},
        headers=CORS_HEADERS,
    )


@router.get("/embed")
async def embed_info(agent_id: str | None = None):
    """iframe URL for a Beyond Presence managed agent (no /v1/calls required)."""
    try:
        async with httpx.AsyncClient() as client:
            aid = (agent_id or await resolve_agent_id(client)).strip()
    except Exception as exc:
        return JSONResponse(
            content={"error": str(exc)},
            headers=CORS_HEADERS,
        )
    return JSONResponse(
        content={
            "agent_id": aid,
            "embed_url": f"https://bey.chat/{aid}",
        },
        headers=CORS_HEADERS,
    )


@router.post("/livekit-session")
async def livekit_session():
    """Start real-time Beyond Presence call (speech-to-speech) via LiveKit."""
    try:
        async with httpx.AsyncClient() as client:
            agent_id = await resolve_agent_id(client)
            data = await create_call(client, agent_id)
        return JSONResponse(
            content={
                "ok": True,
                "call_id": data.get("id"),
                "livekit_url": data.get("livekit_url"),
                "livekit_token": data.get("livekit_token"),
                "agent_id": agent_id,
                "embed_url": f"https://bey.chat/{agent_id}",
                "mode": "realtime_sts",
            },
            headers=CORS_HEADERS,
        )
    except Exception as exc:
        return JSONResponse(
            content={"ok": False, "error": str(exc), "mode": "realtime_sts"},
            headers=CORS_HEADERS,
        )


@router.options("/ask")
async def avatar_ask_options():
    return JSONResponse(content={}, headers=CORS_HEADERS)


@router.post("/ask")
async def avatar_ask(request: AvatarAskRequest):
    """
    Supabase RAG answer → ElevenLabs TTS → Beyond Presence avatar stream.

    Returns grounded reply text, resources for the UI panel, and stream_url.
    """
    try:
        chat = await run_chat(
            message=request.message,
            language=request.language,
            history=request.history,
            session_id=request.session_id,
            voice_mode=True,
        )
        reply = (chat.get("reply") or "").strip()
        if not reply:
            raise ValueError("No reply generated from the knowledge base")

        lang = chat.get("language") or "en"
        avatar = await _stream_avatar_for_text(reply, lang)
        embed_url: str | None = None
        agent_id = ""
        try:
            async with httpx.AsyncClient() as client:
                agent_id = await resolve_agent_id(client)
                embed_url = f"https://bey.chat/{agent_id}"
        except Exception:
            pass
        return JSONResponse(
            content={
                **avatar,
                "reply": reply,
                "resources": chat.get("resources") or [],
                "engine": chat.get("engine"),
                "language": lang,
                "session_id": chat.get("session_id"),
                "agent_id": agent_id or None,
                "embed_url": embed_url,
            },
            headers=CORS_HEADERS,
        )
    except Exception as exc:
        return JSONResponse(
            content={
                "stream_url": None,
                "audio_generated": False,
                "error": str(exc),
                "reply": "",
                "resources": [],
            },
            headers=CORS_HEADERS,
        )


@router.post("")
async def avatar_endpoint(request: AvatarRequest):
    try:
        avatar = await _stream_avatar_for_text(request.text, request.language)
        response = AvatarSuccessResponse(
            stream_url=avatar["stream_url"],
            provider=avatar.get("provider"),
        )
        return JSONResponse(content=response.model_dump(), headers=CORS_HEADERS)
    except Exception as exc:
        error_response = AvatarErrorResponse(error=str(exc))
        return JSONResponse(content=error_response.model_dump(), headers=CORS_HEADERS)
