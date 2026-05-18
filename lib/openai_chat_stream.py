"""OpenAI-compatible SSE streaming helpers for Beyond Presence TTS."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator


def _chunk_payload(
    *,
    chunk_id: str,
    model: str,
    delta: dict,
    finish: str | None = None,
) -> dict:
    choice: dict = {"index": 0, "delta": delta, "finish_reason": finish}
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [choice],
    }


def _sse_line(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def shorten_for_speech(text: str, *, max_chars: int = 480) -> str:
    """Keep Bey TTS reliable — short, plain sentences."""
    body = (text or "").strip()
    body = re.sub(r"\[Voice conversation:.*?\]", "", body, flags=re.I).strip()
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)
    body = re.sub(r"\*([^*]+)\*", r"\1", body)
    body = re.sub(r"`([^`]+)`", r"\1", body)
    body = re.sub(r"#{1,6}\s*", "", body)
    body = re.sub(r"\n{2,}", ". ", body)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars]
    last = cut.rfind(". ")
    if last > 80:
        return cut[: last + 1].strip()
    return cut.strip() + "…"


async def stream_role_assistant_sse(*, model: str = "nila-rag") -> AsyncIterator[str]:
    """First SSE chunk Bey needs before TTS (send immediately while RAG runs)."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    yield _sse_line(
        _chunk_payload(
            chunk_id=chunk_id,
            model=model,
            delta={"role": "assistant", "content": ""},
            finish=None,
        )
    )


async def stream_voice_hold_sse(
    *,
    model: str = "nila-rag",
    chunk_id: str,
    phrase: str = "Let me check the government knowledge base for you.",
) -> AsyncIterator[str]:
    """Short spoken line while RAG runs so Bey keeps TTS active."""
    yield _sse_line(
        _chunk_payload(
            chunk_id=chunk_id,
            model=model,
            delta={"content": phrase},
            finish=None,
        )
    )


async def stream_text_as_openai_sse(
    text: str,
    *,
    model: str = "nila-rag",
    include_role: bool = True,
    chunk_id: str | None = None,
) -> AsyncIterator[str]:
    """
    Stream plain text as OpenAI chat.completion.chunk SSE.

    Bey requires an initial chunk with delta.role=assistant, then content chunks.
    """
    chunk_id = chunk_id or f"chatcmpl-{uuid.uuid4().hex[:24]}"
    body = shorten_for_speech(text)

    if include_role:
        yield _sse_line(
            _chunk_payload(
                chunk_id=chunk_id,
                model=model,
                delta={"role": "assistant", "content": ""},
                finish=None,
            )
        )

    if not body:
        yield _sse_line(
            _chunk_payload(
                chunk_id=chunk_id,
                model=model,
                delta={},
                finish="stop",
            )
        )
        yield "data: [DONE]\n\n"
        return

    # Sentence-sized chunks help TTS start quickly and stay in sync.
    parts = re.split(r"(?<=[.!?])\s+", body)
    if len(parts) <= 1 and len(body) > 120:
        parts = [body[i : i + 120] for i in range(0, len(body), 120)]
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if not piece.endswith((".", "!", "?", "…")) and part != parts[-1]:
            piece += " "
        yield _sse_line(
            _chunk_payload(
                chunk_id=chunk_id,
                model=model,
                delta={"content": piece},
                finish=None,
            )
        )

    yield _sse_line(
        _chunk_payload(
            chunk_id=chunk_id,
            model=model,
            delta={},
            finish="stop",
        )
    )
    yield "data: [DONE]\n\n"


