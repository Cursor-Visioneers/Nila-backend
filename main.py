from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routers import (
    avatar,
    avatar_live,
    chat,
    live,
    live_elevenlabs,
    live_openai,
    n8n_convert,
    reindex,
    resources,
    status,
    voice,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

load_dotenv()

app = FastAPI(title="Nila Backend", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(avatar.router, prefix="/api/avatar", tags=["avatar"])
app.include_router(avatar_live.router, prefix="/api/avatar/live", tags=["avatar-live"])
app.include_router(
    avatar_live.openai_router,
    prefix="/api/avatar/openai/v1",
    tags=["avatar-openai"],
)
app.include_router(reindex.router, prefix="/api/reindex", tags=["reindex"])
app.include_router(resources.router, prefix="/api/resources", tags=["resources"])
app.include_router(n8n_convert.router, prefix="/api/n8n", tags=["n8n"])
app.include_router(status.router, prefix="/api/status", tags=["status"])
app.include_router(voice.router, prefix="/api/voice", tags=["voice"])
app.include_router(live.router, prefix="/api/live", tags=["live"])
app.include_router(live_openai.router, prefix="/api/live/en", tags=["live-en"])
app.include_router(
    live_elevenlabs.router, prefix="/api/live/eleven", tags=["live-eleven"]
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

AVATAR_APP_DIR = Path(__file__).resolve().parent / "frontend/nila-avatar/dist"
if AVATAR_APP_DIR.is_dir():
    app.mount(
        "/avatar-app",
        StaticFiles(directory=str(AVATAR_APP_DIR), html=True),
        name="avatar-app",
    )


@app.get("/test")
def test_ui():
    """Simple HTML UI for avatar / voice testing."""
    page = STATIC_DIR / "avatar-test.html"
    if page.is_file():
        return FileResponse(page)
    return {"error": "static/avatar-test.html not found"}


@app.get("/avatar")
def avatar_beyond_ui():
    """Beyond Presence avatar stream test UI."""
    page = STATIC_DIR / "avatar-beyond.html"
    if page.is_file():
        return FileResponse(page)
    return {"error": "static/avatar-beyond.html not found"}


@app.get("/chat")
def chat_ui():
    """Live multi-turn chat UI."""
    page = STATIC_DIR / "chat-live.html"
    if page.is_file():
        return FileResponse(page)
    return {"error": "static/chat-live.html not found"}


@app.get("/voice")
def voice_ui():
    """Live speech-to-speech agent UI (turn-based)."""
    page = STATIC_DIR / "voice-agent.html"
    if page.is_file():
        return FileResponse(page)
    return {"error": "static/voice-agent.html not found"}


@app.get("/live")
def gemini_live_ui():
    """Gemini Live real-time speech-to-speech UI."""
    page = STATIC_DIR / "gemini-live.html"
    if page.is_file():
        return FileResponse(page)
    return {"error": "static/gemini-live.html not found"}


@app.get("/live-en")
def openai_live_en_ui():
    """OpenAI Realtime full-duplex English speech UI."""
    page = STATIC_DIR / "live-en.html"
    if page.is_file():
        return FileResponse(page)
    return {"error": "static/live-en.html not found"}


@app.get("/live-eleven")
def elevenlabs_live_en_ui():
    """ElevenLabs ConvAI full-duplex English speech UI."""
    page = STATIC_DIR / "live-eleven.html"
    if page.is_file():
        return FileResponse(page)
    return {"error": "static/live-eleven.html not found"}


@app.get("/agent")
def agent_ui():
    """Alias for live voice agent."""
    return voice_ui()


@app.get("/")
def health_check():
    return {"status": "Nila backend online", "version": "1.0"}
