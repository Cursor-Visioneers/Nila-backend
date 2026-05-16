"""Shared helpers for live WebSocket sessions (resources panel)."""

from fastapi import WebSocket


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
) -> None:
    if replace:
        panel[:] = list(new_items)
    else:
        panel.extend(new_items)
        panel[:] = dedupe_resources(panel)
    await websocket.send_json({"type": "resources", "resources": list(panel)})
