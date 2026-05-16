import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lib.chat_service import run_chat

router = APIRouter()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


class ChatRequest(BaseModel):
    message: str
    language: str = "auto"
    history: list[dict] = Field(default_factory=list)
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    engine: str
    language: str
    resources: list[dict]
    session_id: str


@router.options("")
async def chat_options():
    return JSONResponse(content={}, headers=CORS_HEADERS)


@router.post("")
async def chat_endpoint(request: ChatRequest):
    try:
        result = await run_chat(
            message=request.message,
            language=request.language,
            history=request.history,
            session_id=request.session_id,
        )
        response = ChatResponse(**result)
        return JSONResponse(
            content=response.model_dump(),
            headers=CORS_HEADERS,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
            headers=CORS_HEADERS,
        )
