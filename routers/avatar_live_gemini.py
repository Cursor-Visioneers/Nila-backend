"""
Gemini Live multilingual avatar (separate from Beyond Presence).

- Voice: Gemini native audio (English / Sinhala / Tamil)
- Knowledge base: Supabase vector search via Live tools (same as /api/live/ws)
"""

from fastapi import APIRouter, HTTPException, WebSocket

from lib.chat_service import run_chat
from lib.gemini_live_avatar import (
    AVATAR_GEMINI_GREETING,
    AVATAR_GEMINI_LIVE_CONFIG,
    AVATAR_GEMINI_READY_MESSAGE,
    avatar_gemini_model,
    get_genai_client,
)
from lib.bey_presence import api_key as bey_api_key, gemini_lip_sync_enabled
from lib.gemini_live_bridge import GeminiLiveBridgeOptions, run_gemini_live_bridge
from lib.rag_tools import search_government_knowledge_english_kb

router = APIRouter()


def _avatar_greeting() -> str:
    import os

    return (os.getenv("GEMINI_AVATAR_GREETING") or AVATAR_GEMINI_GREETING).strip()


AVATAR_GEMINI_BRIDGE = GeminiLiveBridgeOptions(
    live_config=AVATAR_GEMINI_LIVE_CONFIG,
    model=avatar_gemini_model(),
    ready_message=AVATAR_GEMINI_READY_MESSAGE,
    english_kb_only=True,
    auto_rag_on_transcript=False,
    emit_rag_events=True,
    attach_bey_livekit=True,
    bey_lip_sync=gemini_lip_sync_enabled(),
    auto_rag_inject=False,
    greeting_text=_avatar_greeting(),
    rag_if_no_tool=False,
    rag_await_on_turn_complete=False,
)


def _gemini_ok() -> tuple[bool, str]:
    try:
        get_genai_client()
        return True, ""
    except ValueError as exc:
        return False, str(exc)


def _bey_ok() -> tuple[bool, str]:
    try:
        bey_api_key()
        return True, ""
    except ValueError as exc:
        return False, str(exc)


@router.get("/status")
async def avatar_live_gemini_status():
    """Readiness for Gemini multilingual live avatar with Beyond Presence video."""
    gemini_ok, gemini_message = _gemini_ok()
    bey_ok, bey_message = _bey_ok()
    supabase_ok = False
    vector_docs = 0
    supabase_message = ""

    try:
        from lib.rag import count_documents

        vector_docs = await count_documents()
        supabase_ok = vector_docs > 0
    except Exception as exc:
        supabase_message = str(exc)

    ready = gemini_ok and supabase_ok
    if ready and bey_ok:
        message = (
            "Ready — Gemini voice (EN/SI/TA) + Beyond Presence face via LiveKit."
        )
    elif ready:
        message = (
            "Ready — Gemini voice (EN/SI/TA). Add BEYOND_PRESENCE_API_KEY for agent video."
        )
    elif not gemini_ok:
        message = "Add GEMINI_API_KEY to backend .env."
    elif not supabase_ok:
        message = "Supabase knowledge base empty — run python seed_content.py."
    else:
        message = "Not ready."

    return {
        "provider": "gemini",
        "beyond_presence": bey_ok,
        "beyond_presence_available": bey_ok,
        "bey_ok": bey_ok,
        "bey_message": bey_message if not bey_ok else "",
        "gemini_ok": gemini_ok,
        "gemini_message": gemini_message if not gemini_ok else "",
        "supabase_ok": supabase_ok,
        "vector_docs": vector_docs,
        "supabase_message": supabase_message if not supabase_ok else "",
        "rag_backend": "supabase",
        "kb_search_language": "en",
        "spoken_languages": ["en", "si", "ta"],
        "model": avatar_gemini_model(),
        "websocket": "/api/avatar/live-gemini/ws",
        "ready": ready,
        "message": message,
    }


@router.get("/verify")
async def avatar_live_gemini_verify():
    """Test Gemini Live with the current .env key."""
    try:
        client = get_genai_client()
        model = avatar_gemini_model()
        async with client.aio.live.connect(model=model, config=AVATAR_GEMINI_LIVE_CONFIG):
            pass
        return {
            "ok": True,
            "gemini_live_connect": True,
            "message": "Gemini Live connection succeeded.",
            "model": model,
            "hint": (
                "If you just changed .env, stop uvicorn completely (Ctrl+C) and start again. "
                "`--reload` does not reload environment variables."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "gemini_live_connect": False,
            "message": str(exc),
            "model": avatar_gemini_model(),
        }


@router.get("/rag-test")
async def avatar_live_gemini_rag_test(
    query: str = "How do I register a birth in Sri Lanka?",
):
    """Smoke test: English KB search + optional Sinhala reply via run_chat."""
    try:
        context, resources, _english_query = await search_government_knowledge_english_kb(
            query
        )
        chat = await run_chat(query, language="auto", voice_mode=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "query": query,
        "kb_context_preview": context[:500] + ("…" if len(context) > 500 else ""),
        "answer": chat.get("reply"),
        "engine": chat.get("engine"),
        "language": chat.get("language"),
        "resource_count": len(resources),
        "resources": resources,
        "kb_search_language": "en",
        "supabase": True,
    }


@router.post("/livekit-session")
async def avatar_live_gemini_livekit_session():
    """Retry Bey LiveKit credentials if video failed during the WebSocket session."""
    try:
        from lib.bey_presence import bey_gemini_livekit_room

        bey = await bey_gemini_livekit_room()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **bey}


@router.websocket("/ws")
async def avatar_live_gemini_websocket(websocket: WebSocket):
    """Full-duplex Gemini Live avatar — voice + Supabase tools + optional Bey video."""
    await run_gemini_live_bridge(websocket, AVATAR_GEMINI_BRIDGE)
