"""WebSocket bridge: browser ↔ Gemini Live API with Supabase RAG tools."""

from fastapi import APIRouter, WebSocket

from lib.gemini_live import LIVE_CONFIG, LIVE_MODEL
from lib.gemini_live_bridge import GeminiLiveBridgeOptions, run_gemini_live_bridge

router = APIRouter()

_DEFAULT_BRIDGE = GeminiLiveBridgeOptions(
    live_config=LIVE_CONFIG,
    model=LIVE_MODEL,
)


@router.websocket("/ws")
async def gemini_live_websocket(websocket: WebSocket):
    await run_gemini_live_bridge(websocket, _DEFAULT_BRIDGE)
