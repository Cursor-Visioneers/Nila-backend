"""Shared helpers for live WebSocket sessions (resources panel)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

from lib.rag_tools import select_top_resources


def dedupe_resources(resources: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in resources:
        key = (item.get("type"), item.get("name"), item.get("url"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


async def send_resources(
    websocket: WebSocket,
    panel: list[dict],
    new_items: list[dict],
    *,
    replace: bool = False,
    query: str = "",
    push: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    if replace:
        panel[:] = list(new_items)
    else:
        panel.extend(new_items)
        panel[:] = dedupe_resources(panel)
    panel[:] = select_top_resources(panel, query)
    payload = {"type": "resources", "resources": list(panel)}
    if push is not None:
        push(payload)
    else:
        await websocket.send_json(payload)
