"""Google Gemini API client."""

import asyncio
import os
from functools import lru_cache

import google.generativeai as genai

SYSTEM_INSTRUCTION = (
    "You are Nila, the GIC AI assistant for Sri Lanka. Answer only using the provided "
    "context. Respond in natural Sinhala. If you reference a form, office, or law, prefix "
    "it with RESOURCE: so it can be extracted. If you cannot answer, say so in Sinhala "
    "and suggest calling 1919."
)
MODEL_NAME = "gemini-1.5-pro"


@lru_cache(maxsize=1)
def _configure() -> None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    genai.configure(api_key=api_key)


def _to_gemini_history(history: list) -> list[dict]:
    gemini_history: list[dict] = []
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
        gemini_history.append({"role": role, "parts": [content]})
    return gemini_history


async def generate_sinhala_response(
    user_message: str,
    context: str,
    history: list,
) -> str:
    _configure()
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_INSTRUCTION,
    )
    chat = model.start_chat(history=_to_gemini_history(history))
    prompt = f"CONTEXT:\n{context}\n\nQUESTION: {user_message}"

    def _generate() -> str:
        response = chat.send_message(prompt)
        return response.text or ""

    return await asyncio.to_thread(_generate)
