"""Gemini Live API configuration for real-time speech-to-speech."""

import os

from google import genai
from google.genai import types

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


def get_genai_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    return genai.Client(
        api_key=api_key,
        http_options={"api_version": "v1alpha"},
    )
