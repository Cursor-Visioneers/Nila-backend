"""Shared browser ↔ Gemini Live WebSocket bridge."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from lib.chat_service import run_chat
from lib.gemini_live import LIVE_CONFIG, LIVE_MODEL, friendly_gemini_error, get_genai_client
from lib.language_detector import detect_language
from lib.live_resources import send_resources
from lib.rag_tools import (
    search_government_knowledge_english_kb,
    search_government_knowledge_with_resources,
    should_auto_search_user_text,
)
from lib.openai_chat_stream import shorten_for_speech
from lib.resource_extractor import extract_resources

logger = logging.getLogger(__name__)

TOOL_NAME = "search_government_knowledge"
_RAG_DEBOUNCE_SEC = 2.5
_GEMINI_AUDIO_QUEUE_MAX = 128
_rag_session_lock = asyncio.Lock()


class _ClientOutbound:
    """Serialize all browser WebSocket sends (concurrent send_json drops messages)."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1024)
        self._task: asyncio.Task | None = None
        self._stopped = False

    @property
    def websocket(self) -> WebSocket:
        return self._websocket

    def start(self) -> None:
        self._task = asyncio.create_task(self._writer())

    def push(self, payload: dict) -> None:
        if self._stopped:
            return
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def stop(self) -> None:
        self._stopped = True
        await self._queue.put(None)
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _writer(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            try:
                await self._websocket.send_json(item)
            except Exception:
                break


async def _gemini_audio_uplink(
    session,
    audio_q: asyncio.Queue[bytes],
    stop: asyncio.Event,
    session_io: asyncio.Lock,
    *,
    on_fatal,
) -> None:
    """Forward mic PCM to Gemini; all session sends share one lock to avoid WS races."""
    errors = 0
    while not stop.is_set():
        try:
            pcm = await asyncio.wait_for(audio_q.get(), timeout=0.25)
        except asyncio.TimeoutError:
            continue
        try:
            async with session_io:
                await session.send_realtime_input(
                    audio={"data": pcm, "mime_type": "audio/pcm;rate=16000"},
                )
            errors = 0
        except (ConnectionClosed, ConnectionClosedError, TimeoutError) as exc:
            errors += 1
            logger.warning(
                "Gemini audio uplink failed (%s/5): %s", errors, exc
            )
            if errors >= 5:
                on_fatal()
                return
            await asyncio.sleep(0.08)
        except Exception as exc:
            errors += 1
            logger.warning("Gemini audio uplink error (%s/5): %s", errors, exc)
            if errors >= 5:
                on_fatal()
                return
            await asyncio.sleep(0.05)


def _is_benign_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, WebSocketDisconnect):
        return True
    if isinstance(exc, (ConnectionClosed, ConnectionClosedError, asyncio.CancelledError)):
        return True
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, ExceptionGroup):
        return bool(exc.exceptions) and all(
            _is_benign_disconnect(e) for e in exc.exceptions
        )
    msg = str(exc).lower()
    return any(
        x in msg
        for x in (
            "disconnect",
            "connection closed",
            "keepalive ping timeout",
            "closing connection",
            "no status",
            "1005",
        )
    )


@dataclass(frozen=True)
class GeminiLiveBridgeOptions:
    live_config: dict
    model: str = LIVE_MODEL
    ready_message: str = (
        "Live agent ready — Supabase RAG + resource panel enabled. Use headphones."
    )
    english_kb_only: bool = False
    auto_rag_on_transcript: bool = False
    emit_rag_events: bool = False
    attach_bey_livekit: bool = False
    bey_lip_sync: bool = False
    auto_rag_inject: bool = True
    greeting_text: str | None = None
    rag_await_on_turn_complete: bool = False
    rag_if_no_tool: bool = False


async def _bey_livekit_room() -> dict:
    from lib.bey_presence import bey_gemini_livekit_room

    return await bey_gemini_livekit_room()


async def _start_bey_speech_to_video(room: dict) -> dict:
    from lib.bey_presence import bey_gemini_start_speech_to_video

    return await bey_gemini_start_speech_to_video(room)


async def _push_bey_livekit(websocket: WebSocket, *, room: dict | None = None) -> dict | None:
    """Send LiveKit creds + embed URL so the browser can show the Bey avatar."""
    bey_room: dict | None = room
    try:
        if bey_room is None:
            await websocket.send_json(
                {"type": "status", "message": "Creating Beyond Presence LiveKit room…"}
            )
            bey_room = await _bey_livekit_room()

        video_mode = bey_room.get("video_mode", "agent")
        payload = {
            "type": "livekit",
            "livekit_url": bey_room["livekit_url"],
            "livekit_token": bey_room["livekit_token"],
            "beyond_presence": True,
            "video_mode": video_mode,
            "lip_sync_mode": bey_room.get("lip_sync_mode", "agent"),
            "embed_url": bey_room.get("embed_url"),
            "agent_id": bey_room.get("agent_id"),
            "avatar_id": bey_room.get("avatar_id"),
            "call_id": bey_room.get("call_id"),
        }
        if video_mode == "lip_sync":
            payload["speech_to_video_pending"] = True
        await websocket.send_json(payload)
        await websocket.send_json(
            {
                "type": "status",
                "message": (
                    "Connecting Beyond Presence avatar video…"
                    if video_mode == "agent"
                    else "LiveKit ready — lip-sync will start after you connect."
                ),
            }
        )
        logger.info("Bey livekit room call_id=%s", bey_room.get("call_id"))
        return bey_room
    except Exception as exc:
        logger.warning("Bey LiveKit room failed: %s", exc)
        try:
            await websocket.send_json(
                {
                    "type": "livekit_error",
                    "message": str(exc),
                }
            )
            await websocket.send_json(
                {
                    "type": "status",
                    "message": (
                        "Beyond Presence video unavailable — Gemini voice still works. "
                        f"({exc})"
                    ),
                }
            )
        except Exception:
            pass
        return None


async def _run_kb_search(
    query: str,
    *,
    options: GeminiLiveBridgeOptions,
) -> tuple[str, list[dict], str]:
    if options.english_kb_only:
        return await search_government_knowledge_english_kb(query)
    context, resources = await search_government_knowledge_with_resources(
        query, language="auto"
    )
    return context, resources, query


async def _inject_rag_context_session(
    session, answer: str, user_text: str, *, session_io: asyncio.Lock
) -> None:
    """Inject KB answer without ending the realtime audio session (keeps mic live)."""
    async with session_io:
        await session.send_realtime_input(
            text=_inject_context_text(answer, user_text),
        )


def _send_listening(outbound: _ClientOutbound) -> None:
    outbound.push({"type": "listening"})


async def _send_session_greeting(
    session,
    outbound: _ClientOutbound,
    greeting: str,
    *,
    session_io: asyncio.Lock,
    skip_spoken: bool = False,
) -> None:
    """Prompt Gemini to speak the opening greeting (multilingual avatar)."""
    text = greeting.strip()
    if not text:
        return
    outbound.push({"type": "greeting", "text": text})
    outbound.push(
        {
            "type": "status",
            "message": "Nila is greeting you — then ask your question.",
        }
    )
    if skip_spoken:
        _send_listening(outbound)
        return
    async with session_io:
        await session.send_realtime_input(
            text=(
                "The live session just started. Speak this greeting aloud to the user "
                f"in clear, warm English (2 short sentences max): {text}"
            ),
        )


def _inject_context_text(answer: str, user_text: str) -> str:
    user_lang = detect_language(user_text)
    lang_hint = {
        "si": "Sinhala (සිංහල)",
        "ta": "Tamil (தமிழ்)",
        "en": "English",
    }.get(user_lang, "the user's language")

    return (
        "OFFICIAL ANSWER — Sri Lanka government knowledge base "
        f"(user asked in {lang_hint}):\n\n"
        f"{answer}\n\n"
        "INSTRUCTION: Your very next spoken reply MUST follow the OFFICIAL ANSWER above. "
        f"Speak in {lang_hint}. Use 2–4 short sentences. "
        "Do not invent fees, forms, offices, or URLs. If the user asks a new follow-up question, "
        "you will receive a new OFFICIAL ANSWER block — always use the latest one."
    )


def _emit_rag_search(
    outbound: _ClientOutbound,
    *,
    query: str,
    resources: list[dict],
) -> None:
    outbound.push(
        {
            "type": "rag_search",
            "query": query,
            "resource_count": len(resources),
            "supabase": True,
            "engine": "gemini",
            "kb_language": "en",
        }
    )


def _should_run_auto_rag(text: str, *, options: GeminiLiveBridgeOptions) -> bool:
    """Avatar path: run KB search on every substantive spoken turn, not only English keywords."""
    t = " ".join((text or "").split())
    if len(t) < 8:
        return False
    if options.auto_rag_on_transcript and options.english_kb_only:
        return True
    return should_auto_search_user_text(t)


async def _apply_rag_turn(
    session,
    outbound: _ClientOutbound,
    resource_panel: list[dict],
    user_text: str,
    *,
    options: GeminiLiveBridgeOptions,
    rag_debounce: dict[str, float],
    session_io: asyncio.Lock,
) -> None:
    """Run Supabase RAG + grounded spoken answer (fallback when Live tool was not used)."""
    async with _rag_session_lock:
        await _apply_rag_turn_locked(
            session,
            outbound,
            resource_panel,
            user_text,
            options=options,
            rag_debounce=rag_debounce,
            session_io=session_io,
        )


async def _apply_rag_turn_locked(
    session,
    outbound: _ClientOutbound,
    resource_panel: list[dict],
    user_text: str,
    *,
    options: GeminiLiveBridgeOptions,
    rag_debounce: dict[str, float],
    session_io: asyncio.Lock,
) -> None:
    text = " ".join((user_text or "").split())
    if not _should_run_auto_rag(text, options=options):
        return

    key = text.lower()
    now = time.monotonic()
    if now - rag_debounce.get(key, 0.0) < _RAG_DEBOUNCE_SEC:
        return
    rag_debounce[key] = now

    outbound.push(
        {
            "type": "status",
            "message": "Loading answer from Supabase (English search)…",
        }
    )
    try:
        chat = await run_chat(text, language="auto", voice_mode=True)
        answer = (chat.get("reply") or "").strip()
        found = chat.get("resources") or []
        english_query = text

        if not answer:
            context, found, english_query = await _run_kb_search(
                text, options=options
            )
            answer = context

        await send_resources(
            outbound.websocket,
            resource_panel,
            found,
            replace=True,
            query=english_query,
            push=outbound.push,
        )
        if options.emit_rag_events:
            _emit_rag_search(outbound, query=english_query, resources=found)

        if (
            answer
            and "No matching documents" not in answer
            and options.auto_rag_inject
        ):
            spoken = shorten_for_speech(answer)
            outbound.push(
                {
                    "type": "text",
                    "role": "model",
                    "text": spoken,
                    "final": True,
                    "source": "knowledge_base",
                }
            )
            await _inject_rag_context_session(
                session, spoken, text, session_io=session_io
            )
            if options.emit_rag_events:
                outbound.push(
                    {
                        "type": "rag_applied",
                        "query": english_query,
                        "resource_count": len(found),
                        "supabase": True,
                        "engine": chat.get("engine", "gemini"),
                    }
                )
                outbound.push(
                    {
                        "type": "status",
                        "message": "Answer ready from government knowledge base.",
                    }
                )
            _send_listening(outbound)
    except Exception as exc:
        logger.exception("RAG turn failed")
        outbound.push(
            {"type": "error", "message": f"Knowledge search failed: {exc}"}
        )


async def _handle_tool_call(
    tool_call,
    session,
    outbound: _ClientOutbound,
    resource_panel: list[dict],
    *,
    options: GeminiLiveBridgeOptions,
    rag_debounce: dict[str, float],
    session_io: asyncio.Lock,
) -> None:
    """Run KB tool calls without blocking the live receive loop."""
    function_responses: list[types.FunctionResponse] = []

    for fc in tool_call.function_calls or []:
        if fc.name != TOOL_NAME:
            function_responses.append(
                types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response={"error": f"Unknown tool: {fc.name}"},
                )
            )
            continue

        args = dict(fc.args or {})
        query = str(args.get("query", "")).strip()

        outbound.push(
            {
                "type": "status",
                "message": f"Searching knowledge base (English): {query[:80]}…",
            }
        )

        try:
            chat = await run_chat(query, language="auto", voice_mode=True)
            answer = (chat.get("reply") or "").strip()
            found = chat.get("resources") or []
            if not answer:
                answer, found, _ = await _run_kb_search(query, options=options)

            await send_resources(
                outbound.websocket,
                resource_panel,
                found,
                replace=True,
                query=query,
                push=outbound.push,
            )
            if options.emit_rag_events:
                _emit_rag_search(outbound, query=query, resources=found)

            rag_debounce[query.lower()] = time.monotonic()
            result = answer or "No results."
            if answer:
                outbound.push(
                    {
                        "type": "text",
                        "role": "model",
                        "text": shorten_for_speech(answer),
                        "final": True,
                        "source": "knowledge_base",
                    }
                )
        except Exception as exc:
            result = f"Search failed: {exc}"

        function_responses.append(
            types.FunctionResponse(
                id=fc.id,
                name=fc.name,
                response={"result": result},
            )
        )

    if function_responses:
        async with session_io:
            await session.send_tool_response(function_responses=function_responses)


async def run_gemini_live_bridge(
    websocket: WebSocket,
    options: GeminiLiveBridgeOptions,
) -> None:
    await websocket.accept()

    try:
        client = get_genai_client()
    except ValueError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close()
        return

    resource_panel: list[dict] = []
    turn_transcript = ""
    pending_user_transcript = ""
    rag_debounce: dict[str, float] = {}
    rag_turn_seq = 0
    rag_inflight: asyncio.Task | None = None
    bey_task: asyncio.Task | None = None
    bey_room: dict | None = None
    bey_s2v_started = False

    try:
        if options.attach_bey_livekit:

            async def _deferred_bey() -> None:
                await asyncio.sleep(5.0)
                try:
                    await _push_bey_livekit(websocket)
                except Exception as exc:
                    logger.warning("Bey LiveKit setup failed: %s", exc)

            bey_task = asyncio.create_task(_deferred_bey())

        async with client.aio.live.connect(
            model=options.model,
            config=options.live_config,
        ) as session:
            ready_payload: dict = {
                "type": "ready",
                "provider": "gemini",
                "multilingual": True,
                "kb_search_language": "en" if options.english_kb_only else "auto",
                "model": options.model,
                "beyond_presence_video": options.attach_bey_livekit,
                "video_mode": "lip_sync" if options.bey_lip_sync else "agent",
            }
            outbound = _ClientOutbound(websocket)
            outbound.start()
            outbound.push({"type": "status", "message": options.ready_message})
            outbound.push(ready_payload)
            outbound.push({"type": "resources", "resources": []})
            outbound.push({"type": "accepting_audio", "enabled": True})
            _send_listening(outbound)

            session_io = asyncio.Lock()

            async def schedule_rag(user_text: str) -> None:
                nonlocal rag_inflight, rag_turn_seq
                if not options.auto_rag_on_transcript:
                    return
                text = (user_text or "").strip()
                if not text:
                    return

                turn_id = rag_turn_seq
                rag_turn_seq += 1

                if rag_inflight and not rag_inflight.done():
                    rag_inflight.cancel()

                async def _run() -> None:
                    try:
                        await asyncio.sleep(0.35)
                        if turn_id != rag_turn_seq - 1:
                            return
                        await _apply_rag_turn(
                            session,
                            outbound,
                            resource_panel,
                            text,
                            options=options,
                            rag_debounce=rag_debounce,
                            session_io=session_io,
                        )
                    except asyncio.CancelledError:
                        return
                    except Exception as exc:
                        logger.warning("RAG schedule failed: %s", exc)

                rag_inflight = asyncio.create_task(_run())

            audio_in_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_GEMINI_AUDIO_QUEUE_MAX)
            uplink_stop = asyncio.Event()
            session_lost = False
            model_speaking = False

            def _mark_session_lost() -> None:
                nonlocal session_lost
                if session_lost:
                    return
                session_lost = True
                outbound.push(
                    {
                        "type": "session_lost",
                        "message": (
                            "Voice link to Gemini was lost. "
                            "Click Disconnect, then Connect again."
                        ),
                    }
                )

            uplink_task = asyncio.create_task(
                _gemini_audio_uplink(
                    session,
                    audio_in_q,
                    uplink_stop,
                    session_io,
                    on_fatal=_mark_session_lost,
                )
            )
            mic_chunks_in = 0

            async def browser_to_gemini() -> None:
                nonlocal mic_chunks_in
                try:
                    while True:
                        try:
                            raw = await websocket.receive_text()
                        except WebSocketDisconnect:
                            return
                        msg = json.loads(raw)
                        if msg.get("type") == "audio":
                            if session_lost:
                                continue
                            pcm = base64.b64decode(msg["data"])
                            if len(pcm) < 2:
                                continue
                            mic_chunks_in += 1
                            if mic_chunks_in == 1:
                                outbound.push(
                                    {"type": "mic_ack", "message": "Microphone heard"}
                                )
                                logger.info("Gemini avatar: first mic chunk received")
                            elif mic_chunks_in % 200 == 0:
                                logger.info(
                                    "Gemini avatar mic chunks received: %s",
                                    mic_chunks_in,
                                )
                            try:
                                audio_in_q.put_nowait(pcm)
                            except asyncio.QueueFull:
                                try:
                                    audio_in_q.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass
                                try:
                                    audio_in_q.put_nowait(pcm)
                                except asyncio.QueueFull:
                                    pass
                        elif msg.get("type") == "livekit_ready":
                            if not options.bey_lip_sync:
                                continue
                            nonlocal bey_room, bey_s2v_started
                            if bey_s2v_started:
                                continue
                            if bey_task and not bey_task.done():
                                bey_room = await bey_task
                            if not bey_room:
                                bey_room = await _bey_livekit_room()
                            bey_s2v_started = True
                            try:
                                await websocket.send_json(
                                    {
                                        "type": "status",
                                        "message": "Starting avatar lip-sync…",
                                    }
                                )
                                updated = await _start_bey_speech_to_video(bey_room)
                                bey_room = updated
                                await websocket.send_json(
                                    {
                                        "type": "livekit",
                                        "livekit_url": bey_room["livekit_url"],
                                        "livekit_token": bey_room["livekit_token"],
                                        "beyond_presence": True,
                                        "video_mode": "lip_sync",
                                        "embed_url": bey_room.get("embed_url"),
                                        "speech_to_video_session_id": bey_room.get(
                                            "speech_to_video_session_id"
                                        ),
                                        "speech_to_video_ok": bool(
                                            bey_room.get("speech_to_video_session_id")
                                        ),
                                        "speech_to_video_pending": False,
                                    }
                                )
                            except Exception as exc:
                                logger.warning("Bey speech-to-video failed: %s", exc)
                                await websocket.send_json(
                                    {
                                        "type": "livekit_error",
                                        "message": str(exc),
                                    }
                                )
                        elif msg.get("type") == "text":
                            text = (msg.get("text") or "").strip()
                            if not text:
                                continue
                            async with session_io:
                                await session.send_client_content(
                                    turns=[
                                        {"role": "user", "parts": [{"text": text}]}
                                    ],
                                    turn_complete=True,
                                )
                            await schedule_rag(text)
                except WebSocketDisconnect:
                    return
                except asyncio.CancelledError:
                    return

            async def _run_tool_call(tool_call) -> None:
                try:
                    await _handle_tool_call(
                        tool_call,
                        session,
                        outbound,
                        resource_panel,
                        options=options,
                        rag_debounce=rag_debounce,
                        session_io=session_io,
                    )
                except Exception as exc:
                    logger.exception("Tool call failed: %s", exc)

            async def gemini_to_browser() -> None:
                nonlocal turn_transcript, pending_user_transcript, model_speaking
                turn_had_tool = False
                try:
                    async for chunk in session.receive():
                        if chunk.tool_call:
                            turn_had_tool = True
                            asyncio.create_task(_run_tool_call(chunk.tool_call))
                            continue

                        if chunk.data:
                            if not model_speaking:
                                model_speaking = True
                                outbound.push({"type": "speaking"})
                            outbound.push(
                                {
                                    "type": "audio",
                                    "data": base64.b64encode(chunk.data).decode(
                                        "ascii"
                                    ),
                                    "sample_rate": 24000,
                                    "encoding": "pcm",
                                }
                            )

                        if chunk.text:
                            turn_transcript += chunk.text
                            outbound.push(
                                {
                                    "type": "text",
                                    "role": "model",
                                    "text": chunk.text,
                                }
                            )

                        sc = chunk.server_content
                        if not sc:
                            continue

                        if sc.input_transcription and sc.input_transcription.text:
                            fragment = sc.input_transcription.text
                            pending_user_transcript += fragment
                            outbound.push(
                                {
                                    "type": "text",
                                    "role": "user",
                                    "text": fragment,
                                }
                            )
                        if sc.output_transcription and sc.output_transcription.text:
                            frag = sc.output_transcription.text
                            turn_transcript += frag
                            outbound.push(
                                {
                                    "type": "text",
                                    "role": "model",
                                    "text": frag,
                                }
                            )

                        if sc.interrupted:
                            _send_listening(outbound)

                        if sc.turn_complete:
                            model_speaking = False
                            user_turn = pending_user_transcript.strip()
                            pending_user_transcript = ""
                            if user_turn:
                                outbound.push(
                                    {
                                        "type": "text",
                                        "role": "user",
                                        "text": user_turn,
                                        "final": True,
                                    }
                                )
                            if turn_transcript.strip():
                                full_model = turn_transcript.strip()
                                outbound.push(
                                    {
                                        "type": "text",
                                        "role": "model",
                                        "text": full_model,
                                        "final": True,
                                    }
                                )
                                spoken_resources = extract_resources(full_model)
                                if spoken_resources:
                                    await send_resources(
                                        outbound.websocket,
                                        resource_panel,
                                        spoken_resources,
                                        replace=False,
                                        push=outbound.push,
                                    )
                                turn_transcript = ""

                            if (
                                user_turn
                                and options.auto_rag_on_transcript
                                and (not options.rag_if_no_tool or not turn_had_tool)
                            ):
                                asyncio.create_task(
                                    _apply_rag_turn(
                                        session,
                                        outbound,
                                        resource_panel,
                                        user_turn,
                                        options=options,
                                        rag_debounce=rag_debounce,
                                        session_io=session_io,
                                    )
                                )
                            elif user_turn and options.auto_rag_on_transcript:
                                asyncio.create_task(schedule_rag(user_turn))

                            turn_had_tool = False
                            _send_listening(outbound)
                except (ConnectionClosed, ConnectionClosedError) as exc:
                    logger.warning("Gemini session receive ended: %s", exc)
                    _mark_session_lost()
                    return
                except asyncio.CancelledError:
                    return

            gather_tasks = [
                asyncio.create_task(browser_to_gemini()),
                asyncio.create_task(gemini_to_browser()),
            ]
            if options.greeting_text:

                async def _greeting_task() -> None:
                    # Let mic uplink + receive loop run before greeting competes on session IO.
                    await asyncio.sleep(2.5)
                    try:
                        skip_spoken = mic_chunks_in > 8
                        await _send_session_greeting(
                            session,
                            outbound,
                            options.greeting_text or "",
                            session_io=session_io,
                            skip_spoken=skip_spoken,
                        )
                    except Exception as exc:
                        logger.warning("Session greeting failed: %s", exc)

                gather_tasks.append(asyncio.create_task(_greeting_task()))

            try:
                await asyncio.gather(*gather_tasks, return_exceptions=True)
            finally:
                uplink_stop.set()
                uplink_task.cancel()
                try:
                    await uplink_task
                except asyncio.CancelledError:
                    pass
                await outbound.stop()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        if _is_benign_disconnect(exc):
            logger.debug("Gemini live session ended: %s", exc)
        else:
            logger.exception("Gemini live bridge error")
            try:
                await websocket.send_json(
                    {"type": "error", "message": friendly_gemini_error(exc)}
                )
            except Exception:
                pass
    finally:
        if bey_task and not bey_task.done():
            bey_task.cancel()
