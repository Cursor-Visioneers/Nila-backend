"""Poll Beyond Presence call transcripts and trigger Supabase RAG."""

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


async def poll_call_for_user_speech(
    call_id: str,
    on_user_message,
    *,
    interval_sec: float = 1.5,
    stop: asyncio.Event,
) -> None:
    """
    Poll Bey call messages until stop is set.
    on_user_message(text) is awaited for each new user utterance.
    """
    seen: set[tuple[str, str]] = set()
    last_rag_key = ""

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
                if item.get("sender") != "user":
                    continue
                text = (item.get("message") or "").strip()
                if not text:
                    continue
                sent_at = item.get("sent_at") or ""
                key = (sent_at, text)
                if key in seen:
                    continue
                seen.add(key)

                if not should_auto_search_user_text(text):
                    continue
                rag_key = text.lower()
                if rag_key == last_rag_key:
                    continue
                last_rag_key = rag_key

                try:
                    await on_user_message(text)
                except Exception as exc:
                    logger.warning("RAG handler failed: %s", exc)

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_sec)
            except TimeoutError:
                pass
