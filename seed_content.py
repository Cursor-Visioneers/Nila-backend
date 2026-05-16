#!/usr/bin/env python3
"""Seed Supabase vector store from content/**/*.md files."""

import asyncio
import re
from pathlib import Path

from dotenv import load_dotenv

from lib.rag import CONTENT_DIR, upsert_document

load_dotenv()

LANGUAGE_DIRS = {"en", "si", "ta"}
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return {}, text

    frontmatter: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()

    return frontmatter, text


def _language_from_path(path: Path) -> str:
    rel_parts = path.relative_to(CONTENT_DIR).parts
    if rel_parts and rel_parts[0] in LANGUAGE_DIRS:
        return rel_parts[0]
    return "en"


async def seed() -> None:
    md_files = sorted(CONTENT_DIR.rglob("*.md"))
    if not md_files:
        print("No markdown files found under content/")
        return

    for path in md_files:
        text = path.read_text(encoding="utf-8")
        frontmatter, _ = _parse_frontmatter(text)
        language = _language_from_path(path)
        dept = path.stem
        source_url = frontmatter.get("source_url") or path.relative_to(CONTENT_DIR).as_posix()
        metadata = {
            "filename": path.name,
            "path": path.relative_to(CONTENT_DIR).as_posix(),
            **frontmatter,
        }

        await upsert_document(
            content=text,
            metadata=metadata,
            language=language,
            source_url=source_url,
            dept=dept,
        )
        print(f"Seeded: {path.name} ({language})")

    print(f"\nDone. Seeded {len(md_files)} file(s).")


if __name__ == "__main__":
    asyncio.run(seed())
