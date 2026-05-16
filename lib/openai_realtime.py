"""OpenAI Realtime API (GA) session config for English full-duplex voice."""

import json
import os

# GA realtime model — see https://platform.openai.com/docs/guides/realtime
REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "marin")

SEARCH_GOVERNMENT_KNOWLEDGE_TOOL = {
    "type": "function",
    "name": "search_government_knowledge",
    "description": (
        "Search the Sri Lanka government knowledge base (official services, forms, "
        "fees, offices). Always call this before answering government questions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query",
            },
        },
        "required": ["query"],
    },
}

NILA_REALTIME_INSTRUCTION = """You are Nila, the GIC AI assistant for Sri Lanka government services.

You are in a live English voice conversation. Speak naturally in clear English.
Keep answers short (2–4 sentences) unless the user asks for more detail.

CRITICAL: For ANY question about government services, procedures, forms, fees, or offices,
you MUST call `search_government_knowledge` first and answer ONLY from the returned data.
Do not invent URLs, fees, or office names.

If the knowledge base has no answer, say so and suggest calling 1919.

You are speaking aloud — no markdown, bullet lists, or RESOURCES blocks in speech."""


def realtime_ws_url() -> str:
    return f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"


def realtime_connect_headers(api_key: str) -> dict[str, str]:
    # GA interface — do NOT send OpenAI-Beta: realtime=v1 (beta shape is disabled).
    return {"Authorization": f"Bearer {api_key}"}


def build_session_update_event() -> dict:
    """GA session.update — see platform.openai.com/docs/guides/realtime."""
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": REALTIME_MODEL,
            "output_modalities": ["audio"],
            "instructions": NILA_REALTIME_INSTRUCTION,
            "tools": [SEARCH_GOVERNMENT_KNOWLEDGE_TOOL],
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 450,
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": REALTIME_VOICE,
                },
            },
        },
    }


def extract_audio_delta(event: dict) -> str | None:
    """Base64 PCM chunk from OpenAI (GA and legacy event names)."""
    if event.get("type") in (
        "response.audio.delta",
        "response.output_audio.delta",
    ):
        return event.get("delta") or event.get("audio")
    return None


def extract_function_calls(event: dict) -> list[dict]:
    """Pull completed function_call items from response.done."""
    if event.get("type") != "response.done":
        return []
    response = event.get("response") or {}
    calls: list[dict] = []
    for item in response.get("output") or []:
        if item.get("type") == "function_call" and item.get("status") == "completed":
            calls.append(item)
    return calls


def extract_user_transcript(event: dict) -> str | None:
    et = event.get("type")
    if et == "conversation.item.input_audio_transcription.completed":
        return (event.get("transcript") or "").strip() or None
    if et == "conversation.item.created":
        item = event.get("item") or {}
        if item.get("role") != "user":
            return None
        for part in item.get("content") or []:
            if part.get("type") == "input_audio" and part.get("transcript"):
                return part["transcript"].strip()
    return None


def extract_assistant_transcript(event: dict) -> str | None:
    et = event.get("type")
    if et in (
        "response.audio_transcript.done",
        "response.output_audio_transcript.done",
    ):
        return (event.get("transcript") or "").strip() or None
    if et == "response.content_part.done":
        part = event.get("part") or {}
        if part.get("type") == "audio" and part.get("transcript"):
            return part["transcript"].strip()
    return None


def extract_openai_error_message(event: dict) -> str | None:
    if event.get("type") != "error":
        return None
    err = event.get("error") or {}
    code = err.get("code") or err.get("type") or "error"
    message = err.get("message") or str(err)
    return f"{code}: {message}"


def function_call_output_event(call_id: str, output: dict) -> dict:
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(output),
        },
    }
