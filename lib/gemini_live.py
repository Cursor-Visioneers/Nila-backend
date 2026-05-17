"""Gemini Live API configuration for real-time speech-to-speech."""

import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Native audio model for Live API (Google AI Studio)
LIVE_MODEL = os.getenv(
    "GEMINI_LIVE_MODEL",
    "gemini-2.5-flash-native-audio-preview-12-2025",
)

SEARCH_GOVERNMENT_KNOWLEDGE = types.FunctionDeclaration(
    name="search_government_knowledge",
    description=(
        "Search the Sri Lanka government knowledge base (scraped official services). "
        "Always use this for questions about government procedures, forms, fees, "
        "offices, documents, laws, or departments. Returns relevant excerpts and source URLs."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for, in natural language",
            },
            "language": {
                "type": "string",
                "description": "Document language filter",
                "enum": ["auto", "en", "si", "ta"],
            },
        },
        "required": ["query"],
    },
)

LIVE_TOOLS = [types.Tool(function_declarations=[SEARCH_GOVERNMENT_KNOWLEDGE])]

NILA_LIVE_INSTRUCTION = """You are Nila, the GIC (Government Information Center) AI assistant for Sri Lanka.

You are in a live voice conversation. Speak naturally and keep answers short (2–4 sentences unless the user asks for more).

CRITICAL: For ANY question about government services, you MUST call the tool
`search_government_knowledge` first and answer ONLY using the returned information.
Do not invent fees, forms, or office names.

You can respond in English, Sinhala, or Tamil — match the user's language.
If the knowledge base has no answer, say so and suggest calling 1919.

Be warm and clear. You are speaking aloud, not writing an essay."""

LIVE_CONFIG = {
    "system_instruction": NILA_LIVE_INSTRUCTION,
    "response_modalities": ["AUDIO"],
    "tools": LIVE_TOOLS,
    "input_audio_transcription": {},
    "output_audio_transcription": {},
}


def _load_env() -> None:
    """Re-read .env so key updates apply without guessing whether uvicorn reloaded."""
    load_dotenv(_PROJECT_ROOT / ".env", override=True)


def get_genai_client() -> genai.Client:
    _load_env()
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    return genai.Client(
        api_key=api_key,
        http_options={"api_version": "v1alpha"},
    )


def friendly_gemini_error(exc: Exception) -> str:
    msg = str(exc).strip()
    lower = msg.lower()
    if "expired" in lower:
        return (
            "Your Gemini API key has expired. Open Google AI Studio → API keys, "
            "create a new key, set GEMINI_API_KEY in backend .env, then stop and "
            "restart uvicorn (Ctrl+C, then start again)."
        )
    if "leaked" in lower:
        return (
            "This Gemini API key was reported as leaked by Google and is disabled. "
            "Create a brand-new key in Google AI Studio (do not reuse the old one), "
            "update GEMINI_API_KEY in backend .env, then fully restart uvicorn "
            "(Ctrl+C and start again — --reload does not reload .env)."
        )
    if "api key" in lower or "api_key" in lower or "permission" in lower:
        return (
            f"Gemini rejected the API key: {msg[:200]}. "
            "Check GEMINI_API_KEY in backend .env (no quotes/spaces), then fully "
            "restart the server (stop uvicorn and start again)."
        )
    return msg or "Gemini Live connection failed"


async def verify_gemini_live_connect(
    *,
    model: str | None = None,
    config: dict | None = None,
) -> tuple[bool, str]:
    """Open a short Live session to validate the key (not just that it is set)."""
    model = model or LIVE_MODEL
    config = config or {"response_modalities": ["AUDIO"]}
    try:
        client = get_genai_client()
        async with client.aio.live.connect(model=model, config=config):
            pass
        return True, ""
    except Exception as exc:
        return False, friendly_gemini_error(exc)
