import asyncio
import os
import re

import google.generativeai as genai
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
from pydantic import BaseModel

from lib.rag import upsert_document

router = APIRouter()

OPENAI_MODEL = "gpt-4o"
GEMINI_MODEL = "gemini-3.1-flash-lite"
SI_HINT_PATTERN = re.compile(r"(?:^|[/_\-.])(si)(?:$|[/_\-.])|\(si\)", re.IGNORECASE)


class N8nConvertRequest(BaseModel):
    title: str
    content: str
    dept: str
    url: str
    language: str = "en"


class N8nConvertResponse(BaseModel):
    markdown: str
    dept: str
    url: str
    si_markdown: str | None = None


def _openai_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=api_key)


def _needs_sinhala(language: str, dept: str) -> bool:
    if language == "si":
        return True
    return bool(SI_HINT_PATTERN.search(dept))


def _conversion_prompt(content: str, dept: str, url: str) -> str:
    return (
        "Convert this Sri Lanka government webpage content to clean Markdown. "
        "Extract: service name, required documents, fees, deadlines, office hours, "
        "form names, office addresses. "
        f"Use this frontmatter: ---\nservice_id: {dept}\ndept: {dept}\n"
        f"source_url: {url}\nlanguage: en\n---\n"
        f"Content: {content}"
    )


async def _html_to_markdown(content: str, dept: str, url: str) -> str:
    response = await _openai_client().chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": _conversion_prompt(content, dept, url)}],
    )
    return (response.choices[0].message.content or "").strip()


def _configure_gemini() -> None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    genai.configure(api_key=api_key)


async def _translate_markdown_to_sinhala(markdown: str) -> str:
    _configure_gemini()
    model = genai.GenerativeModel(model_name=GEMINI_MODEL)
    prompt = (
        "Translate the following Sri Lanka government service Markdown into natural "
        "Sinhala Markdown. Preserve YAML frontmatter structure but set language: si. "
        "Keep URLs and structure intact. Return only the translated Markdown.\n\n"
        f"{markdown}"
    )

    def _generate() -> str:
        response = model.generate_content(prompt)
        return (response.text or "").strip()

    return await asyncio.to_thread(_generate)


@router.post("/convert")
async def n8n_convert(request: N8nConvertRequest):
    markdown = await _html_to_markdown(request.content, request.dept, request.url)

    si_markdown: str | None = None
    if _needs_sinhala(request.language, request.dept):
        si_markdown = await _translate_markdown_to_sinhala(markdown)

    metadata = {
        "title": request.title,
        "dept": request.dept,
        "url": request.url,
    }

    await upsert_document(
        content=markdown,
        metadata=metadata,
        language="en",
        source_url=request.url,
        dept=request.dept,
    )

    if si_markdown:
        await upsert_document(
            content=si_markdown,
            metadata={**metadata, "translated": True},
            language="si",
            source_url=request.url,
            dept=request.dept,
        )

    response = N8nConvertResponse(
        markdown=markdown,
        dept=request.dept,
        url=request.url,
        si_markdown=si_markdown,
    )
    return JSONResponse(content=response.model_dump())
