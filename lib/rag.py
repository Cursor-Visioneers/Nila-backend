# -- Run in Supabase SQL editor:
# -- CREATE EXTENSION IF NOT EXISTS vector;
# -- CREATE TABLE documents (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), content text, metadata jsonb, embedding vector(1536), language text DEFAULT 'en', source_url text, dept text);
# -- CREATE OR REPLACE FUNCTION match_documents(query_embedding vector(1536), match_count int, filter_language text DEFAULT NULL)
# -- RETURNS TABLE(id uuid, content text, metadata jsonb, source_url text, dept text, similarity float)
# -- LANGUAGE plpgsql AS $$ BEGIN RETURN QUERY SELECT d.id, d.content, d.metadata, d.source_url, d.dept, 1-(d.embedding<=>query_embedding) AS similarity FROM documents d WHERE (filter_language IS NULL OR d.language=filter_language) ORDER BY d.embedding<=>query_embedding LIMIT match_count; END; $$;

import asyncio
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from openai import AsyncOpenAI
from supabase import Client, create_client

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content"
LANGUAGE_DIRS = {"en", "si", "ta"}
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


@lru_cache(maxsize=1)
def _openai_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=api_key)


@lru_cache(maxsize=1)
def _supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return create_client(url, key)


async def get_embedding(text: str) -> list[float]:
    response = await _openai_client().embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    embedding = response.data[0].embedding
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(f"Expected {EMBEDDING_DIM}-dim embedding, got {len(embedding)}")
    return embedding


async def search_knowledge(
    query: str,
    language: str = "en",
    k: int = 5,
) -> list[dict]:
    embedding = await get_embedding(query)
    client = _supabase_client()

    def _search() -> list[dict]:
        response = client.rpc(
            "match_documents",
            {
                "query_embedding": embedding,
                "match_count": k,
                "filter_language": language,
            },
        ).execute()
        rows = response.data or []
        return [
            {
                "content": row["content"],
                "source_url": row.get("source_url"),
                "dept": row.get("dept"),
                "metadata": row.get("metadata") or {},
                "similarity": row.get("similarity"),
            }
            for row in rows
        ]

    return await asyncio.to_thread(_search)


async def upsert_document(
    content: str,
    metadata: dict,
    language: str,
    source_url: str,
    dept: str,
) -> None:
    embedding = await get_embedding(content)
    client = _supabase_client()
    payload = {
        "content": content,
        "metadata": metadata,
        "embedding": embedding,
        "language": language,
        "source_url": source_url,
        "dept": dept,
    }

    def _upsert() -> None:
        existing = (
            client.table("documents")
            .select("id")
            .eq("dept", dept)
            .eq("language", language)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        if rows:
            client.table("documents").update(payload).eq("id", rows[0]["id"]).execute()
        else:
            client.table("documents").insert(payload).execute()

    await asyncio.to_thread(_upsert)


async def count_documents() -> int:
    client = _supabase_client()

    def _count() -> int:
        response = client.table("documents").select("id", count="exact").execute()
        if response.count is not None:
            return response.count
        return len(response.data or [])

    return await asyncio.to_thread(_count)


def _parse_markdown_path(path: Path) -> tuple[str, str, str, dict]:
    rel = path.relative_to(CONTENT_DIR)
    parts = rel.parts
    language = "en"
    dept_parts = list(parts[:-1])

    if parts and parts[0] in LANGUAGE_DIRS:
        language = parts[0]
        dept_parts = list(parts[1:-1])

    dept = "/".join(dept_parts + [path.stem]) if dept_parts or path.stem else path.stem
    source_url = rel.as_posix()
    metadata = {
        "filename": path.name,
        "path": source_url,
    }
    text = path.read_text(encoding="utf-8")
    return text, language, dept, source_url, metadata


async def reload_vector_store() -> dict:
    md_files = sorted(CONTENT_DIR.rglob("*.md"))
    count = 0

    for path in md_files:
        text, language, dept, source_url, metadata = _parse_markdown_path(path)
        await upsert_document(
            content=text,
            metadata=metadata,
            language=language,
            source_url=source_url,
            dept=dept,
        )
        count += 1

    return {
        "reindexed": count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
