"""ElevenLabs Conversational AI (full-duplex) helpers."""

import os
import re
from functools import lru_cache

import httpx

ELEVEN_BASE = "https://api.elevenlabs.io/v1"
SEARCH_TOOL_NAME = "search_government_knowledge"

NILA_ELEVEN_PROMPT = """You are Nila, the GIC AI assistant for Sri Lanka government services.

You are in a live English voice conversation. Speak naturally and keep answers short (2–4 sentences).

CRITICAL RULES:
1. For government services, forms, fees, or offices, you MUST use ONLY information from
   `search_government_knowledge` or from contextual_update messages labeled OFFICIAL ANSWER.
2. When an OFFICIAL ANSWER block arrives, your very next spoken reply MUST follow it.
   Do not add facts, URLs, fees, or office names that are not in that block.
3. Never invent government information. If the knowledge base has no answer, say so and
   suggest calling 1919."""


class ElevenLabsConvaiError(ValueError):
    """Raised when ConvAI / Agents API is unavailable for this API key."""


def get_api_key() -> str:
    api_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY is not set")
    return api_key


def get_voice_id_en() -> str:
    voice_id = (os.getenv("VOICE_ID_EN") or "").strip()
    if not voice_id:
        raise ValueError("VOICE_ID_EN is not set (ElevenLabs voice for English)")
    return voice_id


def _headers(api_key: str) -> dict[str, str]:
    return {"xi-api-key": api_key, "Content-Type": "application/json"}


def http_error_message(response: httpx.Response, context: str) -> str:
    """Turn ElevenLabs HTTP errors into actionable messages."""
    try:
        body = response.json()
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            status = detail.get("status", "")
            msg = detail.get("message", "")
            if status == "missing_permissions" or response.status_code == 401:
                if "convai" in (msg or "").lower():
                    return (
                        "Your ElevenLabs API key can use TTS but not Conversational AI (Agents). "
                        "Enable Agents on your ElevenLabs plan and create an API key with "
                        "Conversational AI permissions at "
                        "https://elevenlabs.io/app/settings/api-keys - "
                        "then set ELEVENLABS_AGENT_ID in .env. "
                        "Until then, use /live-en for full-duplex English (OpenAI Realtime)."
                    )
                return (
                    f"ElevenLabs API key rejected ({msg or status}). "
                    "Check ELEVENLABS_API_KEY in .env."
                )
            return f"{context}: {msg or detail}"
        if isinstance(detail, str):
            return f"{context}: {detail}"
    except Exception:
        pass
    return f"{context}: HTTP {response.status_code} — {response.text[:240]}"


def _check_response(response: httpx.Response, context: str) -> None:
    if response.is_success:
        return
    raise ElevenLabsConvaiError(http_error_message(response, context))


@lru_cache(maxsize=1)
def get_agent_id_cached() -> str | None:
    agent_id = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
    return agent_id or None


async def check_convai_access() -> dict:
    """
    Verify this API key can use ConvAI (Agents), not just TTS.
    Returns a small status dict for /api/live/eleven/status.
    """
    api_key = get_api_key()
    agent_id = get_agent_id_cached()
    out: dict = {
        "tts_ok": False,
        "convai_ok": False,
        "agent_id_set": bool(agent_id),
        "agent_id": agent_id,
        "message": "",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            voice_id = get_voice_id_en()
            tts = await client.post(
                f"{ELEVEN_BASE}/text-to-speech/{voice_id}",
                headers=_headers(api_key),
                json={"text": "ok", "model_id": "eleven_multilingual_v2"},
            )
            out["tts_ok"] = tts.status_code == 200
        except ValueError:
            out["message"] = "VOICE_ID_EN is not set"

        probe = await client.get(
            f"{ELEVEN_BASE}/convai/conversation/get-signed-url",
            headers={"xi-api-key": api_key},
            params={"agent_id": agent_id or "test"},
        )
        if probe.status_code == 200:
            out["convai_ok"] = True
            out["message"] = "ConvAI ready."
        elif probe.status_code == 404 and agent_id:
            out["convai_ok"] = True
            out["message"] = "ConvAI API reachable (check agent id if connect fails)."
        elif probe.status_code in (401, 403):
            out["message"] = http_error_message(probe, "ConvAI check")
        else:
            out["convai_ok"] = probe.status_code < 500
            out["message"] = http_error_message(probe, "ConvAI check")

    return out


async def get_signed_url(agent_id: str) -> str:
    api_key = get_api_key()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{ELEVEN_BASE}/convai/conversation/get-signed-url",
            headers={"xi-api-key": api_key},
            params={"agent_id": agent_id},
        )
        _check_response(response, "Could not start ElevenLabs live session")
        data = response.json()
    signed = data.get("signed_url")
    if not signed:
        raise ElevenLabsConvaiError("ElevenLabs did not return signed_url")
    return signed


async def ensure_search_tool(client: httpx.AsyncClient, api_key: str) -> str:
    """Create or return existing client tool id for Supabase RAG."""
    existing = os.getenv("ELEVENLABS_RAG_TOOL_ID", "").strip()
    if existing:
        return existing

    payload = {
        "tool_config": {
            "type": "client",
            "name": SEARCH_TOOL_NAME,
            "description": (
                "Search the Sri Lanka government knowledge base. Always use for "
                "government services, forms, fees, or office questions."
            ),
            "expects_response": True,
            "parameters": [
                {
                    "id": "query",
                    "type": "string",
                    "description": "Natural-language search query",
                    "required": True,
                    "value_type": "llm_prompt",
                }
            ],
        }
    }
    response = await client.post(
        f"{ELEVEN_BASE}/convai/tools",
        headers=_headers(api_key),
        json=payload,
    )
    _check_response(response, "Could not create RAG tool on ElevenLabs")
    tool_id = response.json().get("id") or response.json().get("tool_id")
    if not tool_id:
        raise ElevenLabsConvaiError(
            f"Unexpected tool create response: {response.text[:200]}"
        )
    return tool_id


async def ensure_nila_agent() -> str:
    """
    Return ElevenLabs agent id from ELEVENLABS_AGENT_ID.

    Auto-create is only attempted when the API key has convai_write (paid Agents plan).
    """
    cached = get_agent_id_cached()
    if cached:
        return cached

    api_key = get_api_key()
    voice_id = get_voice_id_en()

    async with httpx.AsyncClient(timeout=60.0) as client:
        tool_id = await ensure_search_tool(client, api_key)
        payload = {
            "name": "Nila English Live",
            "conversation_config": {
                "agent": {
                    "first_message": (
                        "Hello! I'm Nila, your Sri Lanka government services assistant. "
                        "How can I help you today?"
                    ),
                    "language": "en",
                    "prompt": {
                        "prompt": NILA_ELEVEN_PROMPT,
                        "llm": os.getenv("ELEVENLABS_AGENT_LLM", "gpt-4o"),
                        "tool_ids": [tool_id],
                        "temperature": 0.3,
                    },
                },
                "asr": {
                    "user_input_audio_format": "pcm_16000",
                },
                "tts": {
                    "voice_id": voice_id,
                    "model_id": os.getenv(
                        "ELEVENLABS_CONVAI_TTS_MODEL", "eleven_flash_v2_5"
                    ),
                },
                "turn": {
                    "turn_eagerness": "normal",
                },
            },
        }
        response = await client.post(
            f"{ELEVEN_BASE}/convai/agents/create",
            headers=_headers(api_key),
            json=payload,
        )
        _check_response(response, "Could not create ElevenLabs agent")
        agent_id = response.json().get("agent_id")
        if not agent_id:
            raise ElevenLabsConvaiError(
                f"Unexpected agent create response: {response.text[:200]}"
            )
        return agent_id


def require_agent_id() -> str:
    """Prefer explicit agent id so we skip auto-provision (needs convai_write)."""
    agent_id = get_agent_id_cached()
    if agent_id:
        return agent_id
    raise ElevenLabsConvaiError(
        "ELEVENLABS_AGENT_ID is not set. Your API key may only have TTS access. "
        "To use /live-eleven: (1) Enable ElevenLabs Agents on your account, "
        "(2) Create an API key with Conversational AI permissions, "
        "(3) Create an agent at https://elevenlabs.io/app/agents with client tool "
        f"`{SEARCH_TOOL_NAME}` (parameter: query), "
        "(4) Set ELEVENLABS_AGENT_ID in .env. "
        "Or use /live-en for full-duplex English without ElevenLabs Agents."
    )


async def resolve_agent_id() -> str:
    """Use configured agent, or auto-create when the key has ConvAI write access."""
    if get_agent_id_cached():
        return get_agent_id_cached()  # type: ignore[return-value]
    try:
        return await ensure_nila_agent()
    except ElevenLabsConvaiError:
        raise
    except httpx.HTTPStatusError as exc:
        raise ElevenLabsConvaiError(
            http_error_message(exc.response, "ElevenLabs Agents")
        ) from exc


def should_send_init_override() -> bool:
    """Send Nila RAG prompt on connect (disable with ELEVENLABS_SEND_INIT=false)."""
    return os.getenv("ELEVENLABS_SEND_INIT", "true").lower() not in ("false", "0", "no")


def build_initiation_client_data() -> dict | None:
    """Per-session prompt override so the agent follows Supabase-backed answers."""
    if not should_send_init_override():
        return None
    return {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": {
            "agent": {
                "language": "en",
                "prompt": {"prompt": NILA_ELEVEN_PROMPT},
            },
        },
    }


def parse_audio_encoding(format_name: str | None) -> str:
    """ConvAI default output is PCM (e.g. pcm_16000), not MP3."""
    if not format_name:
        return "pcm"
    name = format_name.lower()
    if "mp3" in name or "mpeg" in name:
        return "mpeg"
    return "pcm"


def parse_pcm_sample_rate(format_name: str | None, default: int = 16000) -> int:
    if not format_name:
        return default
    name = format_name.lower()
    match = re.search(r"(\d{4,5})", name)
    if match:
        return int(match.group(1))
    if "24000" in name or "24k" in name:
        return 24000
    if "44100" in name:
        return 44100
    if "48000" in name:
        return 48000
    if "22050" in name:
        return 22050
    if "8000" in name:
        return 8000
    return default
