"""Normalize user queries for English-only vector search."""

from lib.language_detector import detect_language


async def to_english_search_query(query: str) -> str:
    """
    Return English keywords for Supabase search.
    English input is unchanged; Sinhala/Tamil are translated via OpenAI.
    """
    q = " ".join((query or "").split())
    if not q:
        return q
    if detect_language(q) == "en":
        return q

    from lib.openai_client import translate_to_english

    return await translate_to_english(q)
