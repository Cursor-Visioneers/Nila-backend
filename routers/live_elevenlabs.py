"""WebSocket bridge: browser ↔ ElevenLabs ConvAI (full-duplex English + Supabase RAG)."""

import asyncio
import base64
import json

import httpx
import websockets
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from lib.elevenlabs_convai import (
    ElevenLabsConvaiError,
    SEARCH_TOOL_NAME,
    build_initiation_client_data,
    check_convai_access,
    get_signed_url,
    parse_audio_encoding,
    parse_pcm_sample_rate,
    resolve_agent_id,
)
from lib.live_resources import send_resources
from lib.chat_service import run_chat
from lib.rag_tools import should_auto_search_user_text

router = APIRouter()


def _ws_close_reason(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def format_bridge_error(exc: BaseException) -> str:
    """Unwrap asyncio TaskGroup / ExceptionGroup so the UI shows the real cause."""
    if isinstance(exc, ExceptionGroup):
        parts = [format_bridge_error(e) for e in exc.exceptions]
        return " | ".join(p for p in parts if p) or str(exc)
    if isinstance(exc, (ConnectionClosed, ConnectionClosedError)):
        code = exc.rcvd.code if exc.rcvd else exc.code
        reason = _ws_close_reason(exc.rcvd.reason if exc.rcvd else exc.reason)
        if "No user message received" in reason:
            return (
                "ElevenLabs closed the session: no microphone audio was received. "
                "Allow mic access, speak after Connect, and keep the tab in focus."
            )
        return f"ElevenLabs connection closed (code {code}): {reason or 'no reason'}"
    return str(exc) or exc.__class__.__name__


def _is_benign_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionClosed, ConnectionClosedError, WebSocketDisconnect)):
        return True
    if isinstance(exc, ExceptionGroup):
        return all(_is_benign_disconnect(e) for e in exc.exceptions)
    return False


@router.get("/status")
async def eleven_status():
    """Check ConvAI access, Supabase RAG, and configured agent."""
    try:
        out = await check_convai_access()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out["rag_tool"] = SEARCH_TOOL_NAME
    out["rag_backend"] = "supabase"
    try:
        from lib.rag import count_documents

        out["vector_docs"] = await count_documents()
        out["supabase_ok"] = True
    except Exception as exc:
        out["vector_docs"] = 0
        out["supabase_ok"] = False
        out["supabase_message"] = str(exc)
    return out


@router.get("/rag-test")
async def eleven_rag_test(query: str = "birth certificate Sri Lanka"):
    """Quick check: Supabase retrieval + grounded spoken answer."""
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
    }


@router.get("/agent-id")
async def eleven_agent_id():
    """Expose configured agent id (for debugging)."""
    try:
        agent_id = await resolve_agent_id()
    except ElevenLabsConvaiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"agent_id": agent_id}


@router.websocket("/ws")
async def elevenlabs_live_websocket(websocket: WebSocket):
    await websocket.accept()
    resource_panel: list[dict] = []
    output_sample_rate = 16000
    output_encoding = "pcm"
    session_ready = asyncio.Event()
    audio_ready = asyncio.Event()
    init_sent = False

    try:
        agent_id = await resolve_agent_id()
        signed_url = await get_signed_url(agent_id)
    except (ElevenLabsConvaiError, ValueError, httpx.HTTPStatusError) as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            from lib.elevenlabs_convai import http_error_message

            message = http_error_message(exc.response, "ElevenLabs live")
        else:
            message = str(exc)
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close()
        return

    try:
        async with websockets.connect(
            signed_url,
            max_size=20 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ) as el_ws:

            async def maybe_send_init() -> None:
                nonlocal init_sent
                if init_sent:
                    return
                payload = build_initiation_client_data()
                if payload:
                    await el_ws.send(json.dumps(payload))
                init_sent = True

            async def browser_to_eleven() -> None:
                try:
                    await asyncio.wait_for(session_ready.wait(), timeout=8.0)
                except TimeoutError:
                    pass
                while True:
                    try:
                        raw = await websocket.receive_text()
                    except WebSocketDisconnect:
                        return
                    msg = json.loads(raw)
                    if msg.get("type") == "audio":
                        pcm = base64.b64decode(msg["data"])
                        if len(pcm) < 2:
                            continue
                        await el_ws.send(
                            json.dumps(
                                {
                                    "user_audio_chunk": base64.b64encode(
                                        pcm
                                    ).decode("ascii")
                                }
                            )
                        )
                    elif msg.get("type") == "text":
                        text = (msg.get("text") or "").strip()
                        if text:
                            await el_ws.send(
                                json.dumps({"type": "user_message", "text": text})
                            )

            async def eleven_keepalive() -> None:
                """Prevent ElevenLabs 60s idle close when user is quiet."""
                while True:
                    await asyncio.sleep(20.0)
                    try:
                        await el_ws.send(json.dumps({"type": "user_activity"}))
                    except (ConnectionClosed, ConnectionClosedError):
                        return

            async def eleven_to_browser() -> None:
                nonlocal output_sample_rate, output_encoding
                last_rag_key = ""
                rag_pending = ""
                rag_inflight: asyncio.Task | None = None

                async def run_rag_search(
                    query: str, *, force: bool = False
                ) -> tuple[str, list[dict]]:
                    """Supabase search + OpenAI answer grounded only on retrieved docs."""
                    nonlocal last_rag_key
                    q = " ".join((query or "").split())
                    if not q:
                        return "No search query provided.", []
                    key = q.lower()
                    if not force and key == last_rag_key:
                        return "", []
                    last_rag_key = key
                    await websocket.send_json(
                        {
                            "type": "status",
                            "message": f"Loading answer from Supabase: {q[:80]}…",
                        }
                    )
                    chat = await run_chat(q, language="en", voice_mode=True)
                    answer = (chat.get("reply") or "").strip()
                    found = chat.get("resources") or []
                    await send_resources(
                        websocket, resource_panel, found, replace=True
                    )
                    await websocket.send_json(
                        {
                            "type": "rag_search",
                            "query": q,
                            "resource_count": len(found),
                            "supabase": True,
                            "engine": chat.get("engine"),
                        }
                    )
                    if answer:
                        await websocket.send_json(
                            {
                                "type": "status",
                                "message": "Answer ready from government knowledge base.",
                            }
                        )
                    return answer, found

                async def inject_rag_context(answer: str, query: str) -> None:
                    if not answer:
                        return
                    await el_ws.send(
                        json.dumps(
                            {
                                "type": "contextual_update",
                                "text": (
                                    f"OFFICIAL ANSWER — Sri Lanka government knowledge base "
                                    f"(user asked: {query}):\n\n"
                                    f"{answer}\n\n"
                                    "INSTRUCTION: Your next spoken response to the user MUST "
                                    "follow the OFFICIAL ANSWER above. Speak it naturally in "
                                    "2–4 sentences. Do NOT add URLs, fees, forms, or offices "
                                    "unless they appear in the OFFICIAL ANSWER. Do NOT "
                                    "contradict it."
                                ),
                            }
                        )
                    )

                async def schedule_auto_rag(transcript: str) -> None:
                    nonlocal rag_inflight, rag_pending
                    if not should_auto_search_user_text(transcript):
                        return
                    rag_pending = transcript.strip()
                    if rag_inflight and not rag_inflight.done():
                        rag_inflight.cancel()

                    async def _fetch_and_inject() -> None:
                        try:
                            await asyncio.sleep(0.2)
                            q = rag_pending
                            if not should_auto_search_user_text(q):
                                return
                            answer, _found = await run_rag_search(q)
                            await inject_rag_context(answer, q)
                        except asyncio.CancelledError:
                            return
                        except Exception as exc:
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": f"Knowledge search failed: {exc}",
                                }
                            )

                    rag_inflight = asyncio.create_task(_fetch_and_inject())

                try:
                    async for message in el_ws:
                        if isinstance(message, bytes):
                            message = message.decode("utf-8", errors="replace")
                        try:
                            event = json.loads(message)
                        except json.JSONDecodeError:
                            continue

                        et = event.get("type")

                        if et == "conversation_initiation_metadata":
                            meta = (
                                event.get(
                                    "conversation_initiation_metadata_event"
                                )
                                or {}
                            )
                            out_fmt = meta.get("agent_output_audio_format")
                            output_sample_rate = parse_pcm_sample_rate(out_fmt, 16000)
                            output_encoding = parse_audio_encoding(out_fmt)
                            input_rate = parse_pcm_sample_rate(
                                meta.get("user_input_audio_format"),
                                16000,
                            )
                            await maybe_send_init()
                            session_ready.set()
                            audio_ready.set()
                            await websocket.send_json(
                                {
                                    "type": "status",
                                    "message": (
                                        "ElevenLabs live ready — speak naturally. "
                                        f"Mic {input_rate} Hz, voice {output_sample_rate} Hz "
                                        f"({output_encoding}). Use headphones."
                                    ),
                                    "audio_encoding": output_encoding,
                                    "audio_sample_rate": output_sample_rate,
                                }
                            )
                            continue

                        if et == "ping":
                            ping = event.get("ping_event") or {}
                            event_id = ping.get("event_id")
                            pong: dict = {"type": "pong"}
                            if event_id is not None:
                                pong["event_id"] = event_id
                            await el_ws.send(json.dumps(pong))
                            continue

                        if et in ("error", "authorization_error", "quota_exceeded"):
                            detail = event.get("message") or event.get("error") or event
                            raise RuntimeError(f"ElevenLabs: {detail}")

                        if et == "interruption":
                            # Do not stop local playback: echo often triggers this
                            # and cuts off the agent after the first chunk.
                            continue

                        if et == "user_transcript":
                            transcript = (
                                event.get("user_transcription_event") or {}
                            ).get("user_transcript", "")
                            if transcript:
                                await websocket.send_json(
                                    {
                                        "type": "text",
                                        "role": "user",
                                        "text": transcript,
                                    }
                                )
                                await schedule_auto_rag(transcript)
                            continue

                        if et == "agent_response":
                            text = (event.get("agent_response_event") or {}).get(
                                "agent_response", ""
                            )
                            if text:
                                await websocket.send_json(
                                    {
                                        "type": "text",
                                        "role": "model",
                                        "text": text,
                                    }
                                )
                            continue

                        if et == "audio":
                            if not audio_ready.is_set():
                                continue
                            audio_event = event.get("audio_event") or {}
                            audio_b64 = (
                                audio_event.get("audio_base_64")
                                or audio_event.get("audio_base64")
                            )
                            if audio_b64:
                                await websocket.send_json(
                                    {
                                        "type": "audio",
                                        "data": audio_b64,
                                        "encoding": output_encoding,
                                        "sample_rate": output_sample_rate,
                                    }
                                )
                            continue

                        if et == "client_tool_call":
                            call = event.get("client_tool_call") or {}
                            tool_name = call.get("tool_name")
                            tool_call_id = call.get("tool_call_id")
                            if tool_name != SEARCH_TOOL_NAME:
                                await el_ws.send(
                                    json.dumps(
                                        {
                                            "type": "client_tool_result",
                                            "tool_call_id": tool_call_id,
                                            "result": json.dumps(
                                                {
                                                    "error": (
                                                        f"Unknown tool: {tool_name}"
                                                    )
                                                }
                                            ),
                                            "is_error": True,
                                        }
                                    )
                                )
                                continue

                            params = call.get("parameters") or {}
                            if isinstance(params, str):
                                try:
                                    params = json.loads(params)
                                except json.JSONDecodeError:
                                    params = {}
                            query = str(
                                params.get("query")
                                or params.get("q")
                                or call.get("query")
                                or ""
                            ).strip()
                            try:
                                result, _found = await run_rag_search(
                                    query, force=True
                                )
                                tool_result = json.dumps({"result": result})
                                is_error = False
                            except Exception as exc:
                                tool_result = json.dumps({"error": str(exc)})
                                is_error = True

                            await el_ws.send(
                                json.dumps(
                                    {
                                        "type": "client_tool_result",
                                        "tool_call_id": tool_call_id,
                                        "result": tool_result,
                                        "is_error": is_error,
                                    }
                                )
                            )
                            continue
                except (ConnectionClosed, ConnectionClosedError):
                    return
                finally:
                    session_ready.set()

            await websocket.send_json(
                {
                    "type": "status",
                    "message": "Connecting to ElevenLabs…",
                }
            )
            await websocket.send_json({"type": "resources", "resources": []})

            # Allow mic forwarding as soon as ElevenLabs socket is up (metadata refines formats).
            session_ready.set()

            async with asyncio.TaskGroup() as tg:
                tg.create_task(browser_to_eleven())
                tg.create_task(eleven_to_browser())
                tg.create_task(eleven_keepalive())

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        if _is_benign_disconnect(exc):
            return
        message = format_bridge_error(exc)
        try:
            await websocket.send_json({"type": "error", "message": message})
        except Exception:
            pass
