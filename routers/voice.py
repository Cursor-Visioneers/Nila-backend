import base64
import json

import httpx
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from lib.chat_service import run_chat
from lib.voice_stt import transcribe_audio
from routers.avatar import _elevenlabs_tts, _voice_id_for_language

router = APIRouter()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


@router.options("/turn")
async def voice_options():
    return JSONResponse(content={}, headers=CORS_HEADERS)


@router.post("/turn")
async def voice_turn(
    audio: UploadFile | None = File(default=None),
    message: str | None = Form(default=None),
    language: str = Form(default="auto"),
    history: str = Form(default="[]"),
    session_id: str | None = Form(default=None),
    voice_mode: str = Form(default="true"),
):
    """
    One voice conversation turn: speech (or text) → Nila reply → spoken audio.

    Send either `audio` (webm/wav from browser) or `message` (pre-transcribed text).
    """
    try:
        history_list = json.loads(history) if history else []

        transcript = (message or "").strip()
        if audio is not None and audio.filename:
            audio_bytes = await audio.read()
            if audio_bytes:
                transcript = await transcribe_audio(audio_bytes, language)

        if not transcript:
            return JSONResponse(
                status_code=400,
                content={"error": "Provide audio or message"},
                headers=CORS_HEADERS,
            )

        is_voice = voice_mode.lower() in ("true", "1", "yes")
        chat_result = await run_chat(
            message=transcript,
            language=language,
            history=history_list,
            session_id=session_id,
            voice_mode=is_voice,
        )

        voice_id = _voice_id_for_language(chat_result["language"])
        async with httpx.AsyncClient() as client:
            audio_bytes = await _elevenlabs_tts(
                client, chat_result["reply"], voice_id
            )
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        return JSONResponse(
            content={
                "transcript": transcript,
                "reply": chat_result["reply"],
                "audio_base64": audio_base64,
                "content_type": "audio/mpeg",
                "engine": chat_result["engine"],
                "language": chat_result["language"],
                "resources": chat_result["resources"],
                "session_id": chat_result["session_id"],
            },
            headers=CORS_HEADERS,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
            headers=CORS_HEADERS,
        )
