"""WebSocket bridge: browser ↔ OpenAI Realtime (full-duplex English + Supabase RAG)."""

import asyncio
import base64
import json
import os

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from lib.live_resources import send_resources
from lib.openai_realtime import (
    build_session_update_event,
    extract_assistant_transcript,
    extract_audio_delta,
    extract_function_calls,
    extract_openai_error_message,
    extract_user_transcript,
    function_call_output_event,
    realtime_connect_headers,
    realtime_ws_url,
)
from lib.rag_tools import search_government_knowledge_with_resources
from lib.resource_extractor import extract_resources

router = APIRouter()

TOOL_NAME = "search_government_knowledge"


async def _run_rag_tool(
    query: str,
    websocket: WebSocket,
    resource_panel: list[dict],
) -> str:
    await websocket.send_json(
        {
            "type": "status",
            "message": f"Searching knowledge base: {query[:80]}…",
        }
    )
    try:
        result, found = await search_government_knowledge_with_resources(
            query, language="en"
        )
        await send_resources(websocket, resource_panel, found, replace=True)
        return result
    except Exception as exc:
        return f"Search failed: {exc}"


async def _handle_function_calls(
    event: dict,
    oai_ws,
    websocket: WebSocket,
    resource_panel: list[dict],
) -> None:
    for call in extract_function_calls(event):
        if call.get("name") != TOOL_NAME:
            continue
        args_raw = call.get("arguments") or "{}"
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            args = {}
        query = str(args.get("query", ""))
        result = await _run_rag_tool(query, websocket, resource_panel)
        await oai_ws.send(
            json.dumps(
                function_call_output_event(
                    call["call_id"],
                    {"result": result},
                )
            )
        )
    if extract_function_calls(event):
        await oai_ws.send(json.dumps({"type": "response.create"}))


@router.websocket("/ws")
async def openai_realtime_websocket(websocket: WebSocket):
    await websocket.accept()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        await websocket.send_json(
            {"type": "error", "message": "OPENAI_API_KEY is not set"}
        )
        await websocket.close()
        return

    resource_panel: list[dict] = []
    assistant_transcript = ""

    try:
        async with websockets.connect(
            realtime_ws_url(),
            additional_headers=realtime_connect_headers(api_key),
            max_size=20 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ) as oai_ws:
            session_ready = asyncio.Event()

            async def configure_session() -> None:
                first = json.loads(await oai_ws.recv())
                if first.get("type") == "error":
                    msg = extract_openai_error_message(first) or str(first)
                    raise RuntimeError(msg)
                await oai_ws.send(json.dumps(build_session_update_event()))
                while True:
                    event = json.loads(await oai_ws.recv())
                    et = event.get("type")
                    if et == "session.updated":
                        session_ready.set()
                        return
                    if et == "error":
                        msg = extract_openai_error_message(event) or str(event)
                        raise RuntimeError(msg)

            await configure_session()

            await websocket.send_json(
                {
                    "type": "status",
                    "message": (
                        "English live agent ready — full duplex, Supabase RAG, "
                        "resources panel. Use headphones."
                    ),
                }
            )
            await websocket.send_json({"type": "resources", "resources": []})

            async def browser_to_openai() -> None:
                await session_ready.wait()
                while True:
                    raw = await websocket.receive_text()
                    msg = json.loads(raw)
                    if msg.get("type") == "audio":
                        pcm = base64.b64decode(msg["data"])
                        await oai_ws.send(
                            json.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": base64.b64encode(pcm).decode("ascii"),
                                }
                            )
                        )
                    elif msg.get("type") == "text":
                        text = (msg.get("text") or "").strip()
                        if not text:
                            continue
                        await oai_ws.send(
                            json.dumps(
                                {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "message",
                                        "role": "user",
                                        "content": [
                                            {"type": "input_text", "text": text}
                                        ],
                                    },
                                }
                            )
                        )
                        await oai_ws.send(json.dumps({"type": "response.create"}))

            async def openai_to_browser() -> None:
                nonlocal assistant_transcript
                async for message in oai_ws:
                    event = json.loads(message)
                    et = event.get("type", "")

                    if et == "error":
                        msg = extract_openai_error_message(event) or str(event)
                        await websocket.send_json({"type": "error", "message": msg})
                        continue

                    if et == "input_audio_buffer.speech_started":
                        assistant_transcript = ""
                        await websocket.send_json({"type": "speech_started"})
                        continue

                    if et == "input_audio_buffer.speech_stopped":
                        await websocket.send_json({"type": "speech_stopped"})
                        continue

                    user_text = extract_user_transcript(event)
                    if user_text:
                        assistant_transcript = ""
                        await websocket.send_json(
                            {"type": "text", "role": "user", "text": user_text}
                        )

                    audio_b64 = extract_audio_delta(event)
                    if audio_b64:
                        await websocket.send_json(
                            {
                                "type": "audio",
                                "data": audio_b64,
                                "sample_rate": 24000,
                            }
                        )

                    asst_text = extract_assistant_transcript(event)
                    if asst_text:
                        assistant_transcript = asst_text
                        await websocket.send_json(
                            {"type": "text", "role": "model", "text": asst_text}
                        )

                    if et == "response.done":
                        if assistant_transcript.strip():
                            spoken = extract_resources(assistant_transcript)
                            if spoken:
                                await send_resources(
                                    websocket,
                                    resource_panel,
                                    spoken,
                                    replace=False,
                                )
                        await _handle_function_calls(
                            event, oai_ws, websocket, resource_panel
                        )

            async with asyncio.TaskGroup() as tg:
                tg.create_task(browser_to_openai())
                tg.create_task(openai_to_browser())

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
