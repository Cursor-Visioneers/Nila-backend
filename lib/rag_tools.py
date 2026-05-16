"""RAG tool handlers for live agents and function calling."""

import re

from lib.language_detector import detect_language
from lib.rag import search_knowledge
from lib.resource_extractor import extract_resources

SUPPORTED = {"en", "si", "ta"}

_GOV_TOPIC = re.compile(
    r"\b("
    r"birth|death|marriage|certificate|passport|visa|license|licence|"
    r"registration|register|government|form|office|fee|fees|nic|"
    r"divisional|secretariat|sri\s*lanka|lankan|colombo|tax|pension|"
    r"apply|application|permit|service|ds\s*office|registrar"
    r")\b",
    re.IGNORECASE,
)


def should_auto_search_user_text(text: str) -> bool:
    """True when live speech should query Supabase without waiting for a tool call."""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    return bool(_GOV_TOPIC.search(t))


def build_context(results: list[dict]) -> str:
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


def _dedupe_resources(resources: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in resources:
        key = (item.get("type"), item.get("name"), item.get("url"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def resources_from_search_results(results: list[dict]) -> list[dict]:
    """Extract forms/offices/laws from retrieved document bodies."""
    resources: list[dict] = []
    for row in results:
        content = row.get("content") or ""
        source_url = row.get("source_url")
        for item in extract_resources(content):
            if not item.get("url") and source_url:
                item = {**item, "url": source_url}
            resources.append(item)
    return _dedupe_resources(resources)


async def search_government_knowledge(query: str, language: str = "auto") -> str:
    """Query Supabase and return formatted context (tool response text)."""
    context, _ = await search_government_knowledge_with_resources(query, language)
    return context


async def search_government_knowledge_with_resources(
    query: str,
    language: str = "auto",
) -> tuple[str, list[dict]]:
    """Query Supabase; return context for the model and resources for the UI panel."""
    query = (query or "").strip()
    if not query:
        return "No search query provided.", []

    if language == "auto":
        lang = detect_language(query)
    elif language in SUPPORTED:
        lang = language
    else:
        lang = "en"

    results = await search_knowledge(query, lang, k=5)
    if not results:
        return (
            "No matching documents in the government knowledge base for this query. "
            "Suggest the user call 1919 for official help."
        ), []

    resources = resources_from_search_results(results)
    context = build_context(results)
    return context, resources
