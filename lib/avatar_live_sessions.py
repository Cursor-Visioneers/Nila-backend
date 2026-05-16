"""Track live avatar WebSocket sessions for resources / status push."""

from __future__ import annotations

import asyncio

from fastapi import WebSocket

_lock = asyncio.Lock()
_sessions: dict[str, WebSocket] = {}
_active_session_id: str | None = None


async def register(session_id: str, websocket: WebSocket) -> None:
    async with _lock:
        _sessions[session_id] = websocket
        global _active_session_id
        _active_session_id = session_id


async def unregister(session_id: str) -> None:
    async with _lock:
        _sessions.pop(session_id, None)
        global _active_session_id
        if _active_session_id == session_id:
            _active_session_id = next(iter(_sessions), None)


def resolve_session_id(header_value: str | None) -> str | None:
    if header_value and header_value.strip():
        return header_value.strip()
    return _active_session_id


async def push(session_id: str | None, payload: dict) -> bool:
    sid = session_id or _active_session_id
    if not sid:
        return False
    async with _lock:
        ws = _sessions.get(sid)
    if not ws:
        return False
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False
