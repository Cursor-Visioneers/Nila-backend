"""RAG tool handlers for live agents and function calling."""

import os
import re

from lib.language_detector import detect_language
from lib.rag import search_knowledge
from lib.resource_extractor import extract_resources

TOP_RESOURCES_LIMIT = max(1, int(os.getenv("NILA_MAX_RESOURCES", "3")))

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


def _query_terms(query: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", (query or "").lower())
        if len(term) > 2
    }


def _chunk_similarity(row: dict, rank: int) -> float:
    similarity = row.get("similarity")
    if similarity is not None:
        try:
            return float(similarity)
        except (TypeError, ValueError):
            pass
    return 1.0 / (rank + 1)


def _relevant_search_rows(results: list[dict], *, max_rows: int = 3) -> list[dict]:
    """Drop weakly related chunks so unrelated resources are not surfaced."""
    if not results:
        return []

    scored_rows = [
        (_chunk_similarity(row, rank), row) for rank, row in enumerate(results)
    ]
    best = max(score for score, _ in scored_rows)
    if best <= 0:
        return [row for _, row in scored_rows[:max_rows]]

    threshold = max(best - 0.06, best * 0.9)
    kept = [row for score, row in scored_rows if score >= threshold]
    if not kept:
        kept = [scored_rows[0][1]]
    return kept[:max_rows]


def _score_resource(
    item: dict,
    query: str,
    *,
    chunk_rank: int = 0,
    similarity: float | None = None,
    dept: str = "",
) -> float:
    terms = _query_terms(query)
    name = (item.get("name") or "").lower()
    url = (item.get("url") or "").lower()
    dept_lower = (dept or "").lower()

    term_hits = sum(1 for term in terms if term in name or term in url)
    dept_hits = sum(1 for term in terms if term in dept_lower)

    if similarity is not None:
        try:
            chunk_score = float(similarity)
        except (TypeError, ValueError):
            chunk_score = 1.0 / (chunk_rank + 1)
    else:
        chunk_score = 1.0 / (chunk_rank + 1)

    return chunk_score * 100.0 + term_hits * 12.0 + dept_hits * 8.0


def _pick_top_diverse(
    ranked: list[tuple[float, dict]],
    limit: int,
) -> list[dict]:
    """Prefer one form, one office, and one law when scores are close."""
    picked: list[dict] = []
    seen_keys: set[tuple] = set()
    seen_types: set[str] = set()

    for score, item in ranked:
        resource_type = item.get("type") or "form"
        key = (resource_type, item.get("name"), item.get("url"))
        if resource_type in seen_types:
            continue
        picked.append(item)
        seen_keys.add(key)
        seen_types.add(resource_type)
        if len(picked) >= limit:
            return picked

    for score, item in ranked:
        key = (item.get("type"), item.get("name"), item.get("url"))
        if key in seen_keys:
            continue
        picked.append(item)
        seen_keys.add(key)
        if len(picked) >= limit:
            break

    return picked


def select_top_resources(
    resources: list[dict],
    query: str = "",
    *,
    limit: int | None = None,
) -> list[dict]:
    """Keep the best-matching resources for the panel (default: top 3)."""
    limit = limit or TOP_RESOURCES_LIMIT
    if not resources:
        return []

    best: dict[tuple, tuple[float, dict]] = {}
    for index, item in enumerate(resources):
        key = (item.get("type"), item.get("name"), item.get("url"))
        score = _score_resource(item, query, chunk_rank=index)
        prev = best.get(key)
        if prev is None or score > prev[0]:
            best[key] = (score, item)

    ranked = sorted(
        best.values(),
        key=lambda pair: (-pair[0], pair[1].get("name") or ""),
    )
    return _pick_top_diverse(ranked, limit)


def resources_from_search_results(
    results: list[dict],
    query: str = "",
    *,
    limit: int | None = None,
) -> list[dict]:
    """Extract forms/offices/laws from retrieved chunks; return top matches only."""
    limit = limit or TOP_RESOURCES_LIMIT
    scored: list[tuple[float, dict]] = []

    for rank, row in enumerate(_relevant_search_rows(results)):
        content = row.get("content") or ""
        source_url = row.get("source_url")
        dept = row.get("dept") or ""
        similarity = _chunk_similarity(row, rank)

        for item in extract_resources(content):
            enriched = dict(item)
            if not enriched.get("url") and source_url:
                enriched["url"] = source_url
            score = _score_resource(
                enriched,
                query,
                chunk_rank=rank,
                similarity=similarity,
                dept=dept,
            )
            scored.append((score, enriched))

    best: dict[tuple, tuple[float, dict]] = {}
    for score, item in scored:
        key = (item.get("type"), item.get("name"), item.get("url"))
        prev = best.get(key)
        if prev is None or score > prev[0]:
            best[key] = (score, item)

    ranked = sorted(
        best.values(),
        key=lambda pair: (-pair[0], pair[1].get("name") or ""),
    )
    return _pick_top_diverse(ranked, limit)


async def search_government_knowledge_english_kb(
    query: str,
) -> tuple[str, list[dict], str]:
    """
    Search the English knowledge base only.
    Non-English queries are translated to English before embedding search.
    Returns (context, resources, english_query_used).
    """
    from lib.search_query import to_english_search_query

    q = await to_english_search_query(query)
    if not q:
        return "No search query provided.", [], ""
    context, resources = await search_government_knowledge_with_resources(
        q, language="en"
    )
    return context, resources, q


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

    resources = resources_from_search_results(results, query=query)
    context = build_context(results)
    return context, resources
