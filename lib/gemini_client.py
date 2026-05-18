"""Google Gemini API client (text / Sinhala chat via google.genai)."""

import os

from google.genai import types

from lib.gemini_live import get_genai_client

SYSTEM_INSTRUCTION = (
    "You are Nila, the GIC AI assistant for Sri Lanka. Answer only using the provided "
    "context. Respond in natural Sinhala. If you reference a form, office, or law, prefix "
    "it with RESOURCE: so it can be extracted. If you cannot answer, say so in Sinhala "
    "and suggest calling 1919."
)
MODEL_NAME = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")


def _to_contents(history: list, user_message: str, context: str) -> list[types.Content]:
    contents: list[types.Content] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        role = msg.get("role", "user")
        if role == "assistant":
            role = "model"
        if role not in ("user", "model"):
            continue
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=content)])
        )
    prompt = f"CONTEXT:\n{context}\n\nQUESTION: {user_message}"
    contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    )
    return contents


async def generate_sinhala_response(
    user_message: str,
    context: str,
    history: list,
) -> str:
    client = get_genai_client()
    response = await client.aio.models.generate_content(
        model=MODEL_NAME,
        contents=_to_contents(history, user_message, context),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.3,
        ),
    )
    return (response.text or "").strip()
