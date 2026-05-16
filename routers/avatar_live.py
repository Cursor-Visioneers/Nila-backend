"""Beyond Presence live avatar: LiveKit + Supabase RAG (like live-eleven)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from lib import avatar_live_sessions as sessions
from lib.bey_call_poller import poll_call_for_user_speech
from lib.bey_presence import (
    create_call,
    ensure_setup,
    llm_api_secret,
    openai_llm_url,
    public_llm_base,
)
from lib.chat_service import run_chat
from lib.live_resources import send_resources
from lib.openai_chat_stream import stream_text_as_openai_sse
from lib.rag_tools import should_auto_search_user_text

import httpx

router = APIRouter()
openai_router = APIRouter()
_llm_completion_count = 0


class LiveSessionRequest(BaseModel):
    public_base_url: str | None = Field(
        default=None,
        description="Optional override for NILA_PUBLIC_BASE_URL (e.g. ngrok URL)",
    )


def _extract_user_message(messages: list[dict[str, Any]]) -> str:
    for item in reversed(messages):
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            return " ".join(parts).strip()
    return ""


def _verify_llm_auth(authorization: str | None) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.removeprefix("Bearer ").removeprefix("bearer ").strip()
    if token != llm_api_secret():
        raise HTTPException(status_code=401, detail="Invalid LLM API token")


@router.get("/status")
async def avatar_live_status():
    supabase_ok = False
    vector_docs = 0
    supabase_message = ""
    try:
        from lib.rag import count_documents

        vector_docs = await count_documents()
        supabase_ok = vector_docs > 0
    except Exception as exc:
        supabase_message = str(exc)

    public_url = public_llm_base()
    bey_ok = _has_bey_key()
    rag_voice_ready = bool(public_url)
    local_mode = not rag_voice_ready
    # Local: LiveKit avatar works without ngrok. Voice RAG needs public URL for Bey to call us.
    ready = bool(bey_ok)

    if ready and rag_voice_ready and supabase_ok:
        message = "Ready — live avatar with Supabase answers in speech."
    elif ready and local_mode and supabase_ok:
        message = (
            "Local mode — avatar speaks with Bey’s LLM; Supabase forms/offices update "
            "automatically when you ask government questions (transcript polling). "
            "Set NILA_PUBLIC_BASE_URL for spoken answers from the knowledge base."
        )
    elif ready and local_mode:
        message = "Local mode — avatar live works. Add Supabase keys for knowledge-base resources."
    elif not bey_ok:
        message = "Add BEYOND_PRESENCE_API_KEY to backend .env."
    else:
        message = "Run POST /api/avatar/setup, then refresh."

    return {
        "beyond_presence_key": bey_ok,
        "supabase_ok": supabase_ok,
        "vector_docs": vector_docs,
        "supabase_message": supabase_message if not supabase_ok else "",
        "public_base_url": public_url or None,
        "rag_llm_ready": rag_voice_ready,
        "local_mode": local_mode,
        "openai_llm_url": openai_llm_url() or None,
        "rag_backend": "supabase",
        "ready": ready,
        "voice_uses_supabase_rag": rag_voice_ready and supabase_ok,
        "resources_from_speech": (
            "external_llm" if rag_voice_ready else "call_transcript_poll"
        ),
        "checks": {
            "beyond_presence": bey_ok,
            "supabase": supabase_ok,
            "public_url": rag_voice_ready,
        },
        "message": message,
    }


@router.get("/rag-test")
async def avatar_live_rag_test(query: str = "How do I register a birth in Sri Lanka?"):
    """Verify Supabase RAG without WebSocket (same data as live answers)."""
    try:
        chat = await run_chat(query, language="en", voice_mode=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "ok": True,
        "query": query,
        "answer": chat.get("reply"),
        "engine": chat.get("engine"),
        "resource_count": len(chat.get("resources") or []),
        "resources": chat.get("resources"),
        "supabase": True,
    }


def _has_bey_key() -> bool:
    from lib.bey_presence import api_key as bey_key

    try:
        bey_key()
        return True
    except ValueError:
        return False


@router.post("/session")
async def create_live_session(body: LiveSessionRequest | None = None):
    """
    Create Bey call + return LiveKit credentials (REST alternative to WebSocket `ready`).
    """
    if body and body.public_base_url:
        import os

        os.environ["NILA_PUBLIC_BASE_URL"] = body.public_base_url.rstrip("/")

    try:
        setup = await ensure_setup()
        async with httpx.AsyncClient() as client:
            call = await create_call(client, setup["agent_id"])
        return {
            "ok": True,
            "session_id": str(uuid.uuid4()),
            "agent_id": setup["agent_id"],
            "livekit_url": call.get("livekit_url"),
            "livekit_token": call.get("livekit_token"),
            "call_id": call.get("id"),
            "rag_enabled": setup.get("rag_enabled"),
            "rag_message": setup.get("rag_message"),
        }
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@router.websocket("/ws")
async def avatar_live_websocket(websocket: WebSocket):
    """
    1) Registers session for resources/status push (from RAG LLM handler).
    2) Configures Bey agent + creates call.
    3) Sends LiveKit credentials — client enables mic and talks.
    """
    await websocket.accept()
    session_id = str(uuid.uuid4())
    resource_panel: list[dict] = []
    await sessions.register(session_id, websocket)
    poller_stop = asyncio.Event()
    poller_task: asyncio.Task | None = None
    supabase_ok = False
    try:
        from lib.rag import count_documents

        supabase_ok = (await count_documents()) > 0
    except Exception:
        pass

    try:
        local = not public_llm_base()

        await websocket.send_json(
            {"type": "status", "message": "Configuring Beyond Presence agent…"}
        )
        setup = await ensure_setup()

        async with httpx.AsyncClient() as client:
            call = await create_call(client, setup["agent_id"])

        call_id = call.get("id") or ""

        if local and call_id and supabase_ok:

            async def on_user_speech(text: str) -> None:
                await _run_rag_for_session(
                    session_id, text, resource_panel, websocket
                )

            poller_task = asyncio.create_task(
                poll_call_for_user_speech(
                    call_id, on_user_speech, stop=poller_stop
                )
            )

        await websocket.send_json(
            {
                "type": "ready",
                "session_id": session_id,
                "agent_id": setup["agent_id"],
                "livekit_url": call.get("livekit_url"),
                "livekit_token": call.get("livekit_token"),
                "call_id": call_id,
                "rag_enabled": setup.get("rag_enabled"),
                "local_mode": local,
                "voice_uses_supabase_rag": setup.get("rag_enabled", False),
                "resources_from_speech": (
                    "external_llm" if setup.get("rag_enabled") else "call_transcript_poll"
                ),
                "embed_url": setup.get("embed_url"),
                "openai_llm_url": setup.get("openai_llm_url"),
            }
        )
        if local and supabase_ok:
            ready_msg = (
                "Connected (local). Speak a government question — resources load from "
                "Supabase automatically. Avatar voice uses Bey until NILA_PUBLIC_BASE_URL is set."
            )
        elif local:
            ready_msg = "Connected (local). Add Supabase keys for knowledge-base resources."
        else:
            ready_msg = setup.get("rag_message") or (
                "Connected — voice and resources use Supabase RAG."
            )
        await websocket.send_json({"type": "status", "message": ready_msg})

        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg.get("type") in ("text", "user_transcript"):
                text = (msg.get("text") or "").strip()
                if text:
                    await _run_rag_for_session(
                        session_id, text, resource_panel, websocket
                    )

    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        poller_stop.set()
        if poller_task:
            poller_task.cancel()
            try:
                await poller_task
            except asyncio.CancelledError:
                pass
        await sessions.unregister(session_id)


async def _run_rag_for_session(
    session_id: str,
    query: str,
    panel: list[dict],
    websocket: WebSocket,
) -> None:
    await websocket.send_json(
        {
            "type": "status",
            "message": f"Loading answer from Supabase: {query[:80]}…",
        }
    )
    chat = await run_chat(query, language="en", voice_mode=True)
    answer = (chat.get("reply") or "").strip()
    found = chat.get("resources") or []
    await send_resources(websocket, panel, found, replace=True)
    await websocket.send_json(
        {
            "type": "rag_search",
            "query": query,
            "resource_count": len(found),
            "supabase": True,
            "engine": chat.get("engine"),
        }
    )
    if answer:
        await websocket.send_json(
            {
                "type": "text",
                "role": "model",
                "text": answer,
            }
        )
        await websocket.send_json(
            {
                "type": "rag_applied",
                "query": query,
                "resource_count": len(found),
                "engine": chat.get("engine"),
                "supabase": True,
            }
        )
        await websocket.send_json(
            {
                "type": "status",
                "message": "Answer ready from government knowledge base.",
            }
        )


@openai_router.post("/chat/completions")
async def openai_chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_nila_session_id: str | None = Header(default=None, alias="X-Nila-Session-Id"),
):
    """
    OpenAI-compatible endpoint Bey calls for each agent turn.
    Runs Supabase RAG and streams the grounded spoken answer.
    """
    global _llm_completion_count
    _verify_llm_auth(authorization)
    _llm_completion_count += 1
    body = await request.json()
    messages = body.get("messages") or []
    user_text = _extract_user_message(messages)
    if not user_text:
        raise HTTPException(status_code=400, detail="No user message in messages")

    model = body.get("model") or "nila-rag"
    stream = bool(body.get("stream", True))
    session_id = x_nila_session_id

    await sessions.push(
        session_id,
        {"type": "text", "role": "user", "text": user_text},
    )

    if should_auto_search_user_text(user_text):
        await sessions.push(
            session_id,
            {
                "type": "status",
                "message": f"Loading answer from Supabase: {user_text[:80]}…",
            },
        )

    chat = await run_chat(user_text, language="en", voice_mode=True)
    answer = (chat.get("reply") or "").strip()
    if not answer:
        answer = (
            "I could not find that in the government knowledge base. "
            "Try rephrasing or call 1919 for help."
        )

    resources = chat.get("resources") or []
    await sessions.push(
        session_id,
        {"type": "resources", "resources": resources},
    )
    await sessions.push(
        session_id,
        {
            "type": "rag_search",
            "query": user_text,
            "resource_count": len(resources),
            "supabase": True,
            "engine": chat.get("engine"),
        },
    )
    await sessions.push(
        session_id,
        {"type": "text", "role": "model", "text": answer},
    )

    if not stream:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "model": model,
        }

    return StreamingResponse(
        stream_text_as_openai_sse(answer, model=model),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
