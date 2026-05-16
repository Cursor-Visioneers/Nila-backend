"""OpenAI-compatible SSE streaming helpers."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator


def _chunk_payload(
    *,
    chunk_id: str,
    model: str,
    content: str | None = None,
    finish: str | None = None,
) -> dict:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    choice: dict = {"index": 0, "delta": delta}
    if finish:
        choice["finish_reason"] = finish
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [choice],
    }


async def stream_text_as_openai_sse(
    text: str,
    *,
    model: str = "nila-rag",
) -> AsyncIterator[str]:
    """Stream plain text as OpenAI chat.completion.chunk SSE."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    body = (text or "").strip()
    if not body:
        yield f"data: {json.dumps(_chunk_payload(chunk_id=chunk_id, model=model, content='', finish='stop'))}\n\n"
        yield "data: [DONE]\n\n"
        return

    words = body.split()
    for index, word in enumerate(words):
        piece = word if index == len(words) - 1 else f"{word} "
        yield f"data: {json.dumps(_chunk_payload(chunk_id=chunk_id, model=model, content=piece))}\n\n"

    yield f"data: {json.dumps(_chunk_payload(chunk_id=chunk_id, model=model, finish='stop'))}\n\n"
    yield "data: [DONE]\n\n"
