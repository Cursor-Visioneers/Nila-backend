"""Poll Beyond Presence call transcripts and push them to the live UI."""

from __future__ import annotations

import asyncio
import logging

import httpx

from lib.bey_presence import api_base, bey_headers
from lib.rag_tools import should_auto_search_user_text

logger = logging.getLogger(__name__)


async def list_call_messages(client: httpx.AsyncClient, call_id: str) -> list[dict]:
    response = await client.get(
        f"{api_base()}/v1/calls/{call_id}/messages",
        headers=bey_headers(),
        timeout=30.0,
    )
    if not response.is_success:
        return []
    data = response.json()
    if isinstance(data, list):
        return data
    return data.get("data") or []


async def poll_call_transcripts(
    call_id: str,
    on_message,
    *,
    interval_sec: float = 1.2,
    stop: asyncio.Event,
) -> None:
    """
    Poll Bey call messages until stop is set.

    on_message(sender, text) is awaited for each new utterance.
    sender is typically "user" or "agent".
    """
    seen: set[tuple[str, str, str]] = set()

    async with httpx.AsyncClient() as client:
        while not stop.is_set():
            try:
                messages = await list_call_messages(client, call_id)
            except Exception as exc:
                logger.warning("call message poll failed: %s", exc)
                await asyncio.sleep(interval_sec)
                continue

            for item in messages:
                if not isinstance(item, dict):
                    continue
                sender = (item.get("sender") or item.get("role") or "").strip().lower()
                if sender not in ("user", "agent", "assistant"):
                    continue
                if sender == "assistant":
                    sender = "agent"
                text = (item.get("message") or item.get("text") or "").strip()
                if not text:
                    continue
                sent_at = str(item.get("sent_at") or item.get("created_at") or "")
                key = (sent_at, sender, text)
                if key in seen:
                    continue
                seen.add(key)

                try:
                    await on_message(sender, text)
                except Exception as exc:
                    logger.warning("call transcript handler failed: %s", exc)

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_sec)
            except TimeoutError:
                pass


async def poll_call_for_user_speech(
    call_id: str,
    on_user_message,
    *,
    interval_sec: float = 1.5,
    stop: asyncio.Event,
) -> None:
    """Backward-compatible wrapper: only user messages that pass gov-topic filter."""

    async def _on_message(sender: str, text: str) -> None:
        if sender != "user":
            return
        if not should_auto_search_user_text(text):
            return
        await on_user_message(text)

    await poll_call_transcripts(
        call_id, _on_message, interval_sec=interval_sec, stop=stop
    )
