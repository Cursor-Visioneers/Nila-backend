"""Gemini Live config for multilingual avatar (English KB search, spoken SI/TA/EN)."""

import os

from google.genai import types

from lib.gemini_live import LIVE_MODEL, get_genai_client

TOOL_NAME = "search_government_knowledge"

SEARCH_GOVERNMENT_KNOWLEDGE_EN = types.FunctionDeclaration(
    name=TOOL_NAME,
    description=(
        "Search the Sri Lanka government knowledge base (English documents only). "
        "Always use for government procedures, forms, fees, offices, or laws. "
        "The query parameter MUST be in English — translate from the user's language first."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "English search keywords (translate Sinhala/Tamil user speech to English)"
                ),
            },
        },
        "required": ["query"],
    },
)

AVATAR_GEMINI_TOOLS = [types.Tool(function_declarations=[SEARCH_GOVERNMENT_KNOWLEDGE_EN])]

NILA_AVATAR_GEMINI_INSTRUCTION = """You are Nila, the GIC AI assistant for Sri Lanka government services.

You are in a live voice conversation with a visual avatar. Speak naturally in short answers (2–4 sentences).

CRITICAL RULES:
1. Always listen when the user speaks. Respond to what they said — do not ignore them.
2. For ANY government-services question, call `search_government_knowledge` first (English query;
   translate from Sinhala/Tamil). Answer ONLY from tool results.
3. Never invent fees, forms, offices, or URLs.
4. Speak in the SAME language the user uses — English, Sinhala (සිංහල), or Tamil (தமிழ்).
5. If the knowledge base has no answer, say so in the user's language and suggest calling 1919.
6. After you finish speaking, stop and listen for the next question.

You are speaking aloud, not writing an essay. Be warm and clear."""

AVATAR_GEMINI_GREETING = (
    "Hello! I'm Nila, your Sri Lanka government services assistant. "
    "How can I help you today?"
)

AVATAR_GEMINI_LIVE_CONFIG = {
    "tools": AVATAR_GEMINI_TOOLS,
    "system_instruction": NILA_AVATAR_GEMINI_INSTRUCTION,
    "response_modalities": ["AUDIO"],
    "input_audio_transcription": {},
    "output_audio_transcription": {},
}

AVATAR_GEMINI_READY_MESSAGE = (
    "Connected — Nila will greet you, then listen in English, Sinhala, or Tamil. "
    "Use headphones to avoid echo."
)


def avatar_gemini_model() -> str:
    return os.getenv("GEMINI_AVATAR_LIVE_MODEL", LIVE_MODEL)


__all__ = [
    "AVATAR_GEMINI_GREETING",
    "AVATAR_GEMINI_LIVE_CONFIG",
    "AVATAR_GEMINI_READY_MESSAGE",
    "SEARCH_GOVERNMENT_KNOWLEDGE_EN",
    "TOOL_NAME",
    "avatar_gemini_model",
    "get_genai_client",
]
