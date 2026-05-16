"""WebSocket bridge: browser ↔ Gemini Live API with Supabase RAG tools."""

import asyncio
import base64
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.genai import types

from lib.gemini_live import LIVE_CONFIG, LIVE_MODEL, get_genai_client
from lib.rag_tools import search_government_knowledge_with_resources
from lib.resource_extractor import extract_resources

router = APIRouter()

TOOL_NAME = "search_government_knowledge"


def _dedupe_resources(resources: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in resources:
        key = (item.get("type"), item.get("name"), item.get("url"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


async def _send_resources(
    websocket: WebSocket,
    panel: list[dict],
    new_items: list[dict],
    *,
    replace: bool = False,
) -> None:
    if replace:
        panel[:] = list(new_items)
    else:
        panel.extend(new_items)
        panel[:] = _dedupe_resources(panel)
    await websocket.send_json({"type": "resources", "resources": list(panel)})


async def _handle_tool_call(
    tool_call,
    websocket: WebSocket,
    session,
    resource_panel: list[dict],
) -> None:
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
        query = str(args.get("query", ""))
        language = str(args.get("language", "auto"))

        await websocket.send_json(
            {
                "type": "status",
                "message": f"Searching knowledge base: {query[:80]}…",
            }
        )

        try:
            result, found = await search_government_knowledge_with_resources(
                query, language
            )
            await _send_resources(websocket, resource_panel, found, replace=True)
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
        await session.send_tool_response(function_responses=function_responses)


@router.websocket("/ws")
async def gemini_live_websocket(websocket: WebSocket):
    await websocket.accept()

    try:
        client = get_genai_client()
    except ValueError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close()
        return

    resource_panel: list[dict] = []
    turn_transcript = ""

    try:
        async with client.aio.live.connect(model=LIVE_MODEL, config=LIVE_CONFIG) as session:
            await websocket.send_json(
                {
                    "type": "status",
                    "message": (
                        "Live agent ready — Supabase RAG + resource panel enabled. "
                        "Use headphones."
                    ),
                }
            )
            await websocket.send_json({"type": "resources", "resources": []})

            async def browser_to_gemini() -> None:
                while True:
                    raw = await websocket.receive_text()
                    msg = json.loads(raw)
                    if msg.get("type") == "audio":
                        pcm = base64.b64decode(msg["data"])
                        await session.send_realtime_input(
                            audio={"data": pcm, "mime_type": "audio/pcm"}
                        )
                    elif msg.get("type") == "text":
                        await session.send_client_content(
                            turns=[{"role": "user", "parts": [{"text": msg["text"]}]}],
                            turn_complete=True,
                        )

            async def gemini_to_browser() -> None:
                nonlocal turn_transcript
                async for chunk in session.receive():
                    if chunk.tool_call:
                        await _handle_tool_call(
                            chunk.tool_call, websocket, session, resource_panel
                        )
                        continue

                    sc = chunk.server_content
                    if sc:
                        if sc.input_transcription and sc.input_transcription.text:
                            turn_transcript = ""
                            await websocket.send_json(
                                {
                                    "type": "text",
                                    "role": "user",
                                    "text": sc.input_transcription.text,
                                }
                            )

                        if sc.output_transcription and sc.output_transcription.text:
                            turn_transcript += sc.output_transcription.text
                            await websocket.send_json(
                                {
                                    "type": "text",
                                    "role": "model",
                                    "text": sc.output_transcription.text,
                                }
                            )

                        if sc.turn_complete and turn_transcript.strip():
                            spoken_resources = extract_resources(turn_transcript)
                            if spoken_resources:
                                await _send_resources(
                                    websocket,
                                    resource_panel,
                                    spoken_resources,
                                    replace=False,
                                )
                            turn_transcript = ""

                        if chunk.data:
                            await websocket.send_json(
                                {
                                    "type": "audio",
                                    "data": base64.b64encode(chunk.data).decode(
                                        "ascii"
                                    ),
                                    "sample_rate": 24000,
                                }
                            )
                        elif chunk.text:
                            await websocket.send_json(
                                {
                                    "type": "text",
                                    "role": "model",
                                    "text": chunk.text,
                                }
                            )

            async with asyncio.TaskGroup() as tg:
                tg.create_task(browser_to_gemini())
                tg.create_task(gemini_to_browser())

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
