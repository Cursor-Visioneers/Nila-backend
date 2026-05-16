import re
import uuid
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lib.gemini_client import generate_sinhala_response
from lib.language_detector import detect_language
from lib.openai_client import generate_response
from lib.rag import search_knowledge
from lib.resource_extractor import extract_resources, strip_resources_from_reply

router = APIRouter()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}

PANEL_FOOTERS = {
    "en": (
        "You can find the relevant forms and office details in the panel on the right."
    ),
    "si": "අදාළ ලේඛන සහ කාර්යාල තොරතුරු දකුණු පැත්තේ පෙනෙනු ඇත.",
    "ta": (
        "சம்பந்தப்பட்ட படிவங்கள் மற்றும் அலுவலக விவரங்களை "
        "வலதுபுற panel-இல் காணலாம்."
    ),
}

SUPPORTED_LANGUAGES = {"en", "si", "ta"}


class ChatRequest(BaseModel):
    message: str
    language: str = "auto"
    history: list[dict] = Field(default_factory=list)
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    engine: Literal["openai", "gemini"]
    language: str
    resources: list[dict]
    session_id: str


def _resolve_language(message: str, language: str) -> str:
    if language == "auto":
        return detect_language(message)
    if language in SUPPORTED_LANGUAGES:
        return language
    return detect_language(message)


def _build_context(results: list[dict]) -> str:
    if not results:
        return "No relevant knowledge found."

    parts: list[str] = []
    for index, row in enumerate(results, start=1):
        source_url = row.get("source_url") or "unknown"
        dept = row.get("dept") or "unknown"
        content = row.get("content") or ""
        parts.append(
            f"[{index}] Source: {source_url} | Department: {dept}\n{content}"
        )
    return "\n\n".join(parts)


def _finalize_reply(raw_reply: str, language: str, resources: list[dict]) -> str:
    reply = strip_resources_from_reply(raw_reply)
    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()

    if resources:
        footer = PANEL_FOOTERS.get(language, PANEL_FOOTERS["en"])
        if reply and not reply.endswith(footer):
            reply = f"{reply}\n\n{footer}"

    return reply


@router.options("")
async def chat_options():
    return JSONResponse(content={}, headers=CORS_HEADERS)


@router.post("")
async def chat_endpoint(request: ChatRequest):
    try:
        language = _resolve_language(request.message, request.language)
        session_id = request.session_id or str(uuid.uuid4())

        results = await search_knowledge(request.message, language)
        context = _build_context(results)

        if language == "si":
            raw_reply = await generate_sinhala_response(
                user_message=request.message,
                context=context,
                history=request.history,
            )
            engine: Literal["openai", "gemini"] = "gemini"
        else:
            raw_reply = await generate_response(
                user_message=request.message,
                context=context,
                history=request.history,
                language=language,
            )
            engine = "openai"

        resources = extract_resources(raw_reply)
        reply = _finalize_reply(raw_reply, language, resources)

        response = ChatResponse(
            reply=reply,
            engine=engine,
            language=language,
            resources=resources,
            session_id=session_id,
        )
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
