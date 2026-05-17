import re
import uuid
from typing import Literal

from lib.gemini_client import generate_sinhala_response
from lib.language_detector import detect_language
from lib.openai_client import generate_response
from lib.rag import search_knowledge
from lib.rag_tools import (
    build_context,
    resources_from_search_results,
    select_top_resources,
)
from lib.resource_extractor import extract_resources, strip_resources_from_reply

SUPPORTED_LANGUAGES = {"en", "si", "ta"}

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


def resolve_language(message: str, language: str) -> str:
    if language == "auto":
        return detect_language(message)
    if language in SUPPORTED_LANGUAGES:
        return language
    return detect_language(message)


def finalize_reply(
    raw_reply: str,
    language: str,
    resources: list[dict],
    *,
    voice_mode: bool = False,
) -> str:
    reply = strip_resources_from_reply(raw_reply)
    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()

    if resources and not voice_mode:
        footer = PANEL_FOOTERS.get(language, PANEL_FOOTERS["en"])
        if reply and not reply.endswith(footer):
            reply = f"{reply}\n\n{footer}"

    return reply


VOICE_HINT = (
    "\n\n[Voice conversation: reply in 2–4 short spoken sentences only. "
    "No markdown, bullets, or RESOURCES block.]"
)


async def run_chat(
    message: str,
    language: str = "auto",
    history: list[dict] | None = None,
    session_id: str | None = None,
    *,
    voice_mode: bool = False,
) -> dict:
    history = history or []
    resolved = resolve_language(message, language)
    sid = session_id or str(uuid.uuid4())
    user_message = message + VOICE_HINT if voice_mode else message

    results = await search_knowledge(message, resolved)
    context = build_context(results)

    if resolved == "si":
        raw_reply = await generate_sinhala_response(
            user_message=user_message,
            context=context,
            history=history,
        )
        engine: Literal["openai", "gemini"] = "gemini"
    else:
        raw_reply = await generate_response(
            user_message=user_message,
            context=context,
            history=history,
            language=resolved,
        )
        engine = "openai"

    kb_resources = resources_from_search_results(results, query=message)
    llm_resources = extract_resources(raw_reply)
    resources = select_top_resources(kb_resources + llm_resources, message)

    reply = finalize_reply(raw_reply, resolved, resources, voice_mode=voice_mode)

    return {
        "reply": reply,
        "engine": engine,
        "language": resolved,
        "resources": resources,
        "session_id": sid,
    }
