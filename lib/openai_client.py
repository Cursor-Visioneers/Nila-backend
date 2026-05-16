"""OpenAI API client."""

import os
from functools import lru_cache

from openai import AsyncOpenAI

MODEL_NAME = "gpt-4o"


@lru_cache(maxsize=1)
def _client() -> AsyncOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=api_key)


def _system_prompt(language: str, context: str) -> str:
    return (
        "You are Nila, GIC's AI assistant for Sri Lanka government services. "
        "Answer ONLY using the retrieved knowledge below. "
        f"Language: {language}. "
        "Always extract forms, offices, laws, deadlines into a RESOURCES: section at the "
        "end of your response using this exact format:\n"
        "RESOURCES:\n"
        "- FORM: [form name] | [source_url]\n"
        "- OFFICE: [office name] | [address or url]\n"
        "- LAW: [law name] | [source_url]\n"
        "If you cannot answer, say so and suggest calling 1919.\n"
        f"KNOWLEDGE:\n{context}"
    )


def _to_openai_history(history: list) -> list[dict]:
    messages: list[dict] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        role = msg.get("role", "user")
        if role == "model":
            role = "assistant"
        if role not in ("user", "assistant"):
            continue
        messages.append({"role": role, "content": content})
    return messages


async def generate_response(
    user_message: str,
    context: str,
    history: list,
    language: str,
) -> str:
    messages = [
        {"role": "system", "content": _system_prompt(language, context)},
        *_to_openai_history(history),
        {"role": "user", "content": user_message},
    ]
    response = await _client().chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
    )
    return response.choices[0].message.content or ""
