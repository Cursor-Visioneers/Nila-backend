import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from lib.rag import count_documents

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNCED_DIR = PROJECT_ROOT / "content" / "synced"
N8N_SITES_FILE = SYNCED_DIR / "n8n_sites.json"
SYNCED_AT_FILE = SYNCED_DIR / "synced_at.txt"
DEFAULT_N8N_SITES = 312


def _read_n8n_sites() -> int:
    if not N8N_SITES_FILE.exists():
        return DEFAULT_N8N_SITES
    try:
        data = json.loads(N8N_SITES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "n8n_sites" in data:
            return int(data["n8n_sites"])
        if isinstance(data, int):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_N8N_SITES


def _read_last_sync() -> str:
    if not SYNCED_AT_FILE.exists():
        return "never"
    try:
        value = SYNCED_AT_FILE.read_text(encoding="utf-8").strip()
        return value or "never"
    except OSError:
        return "never"


@router.get("")
async def status_endpoint():
    try:
        vector_docs = await count_documents()
    except Exception:
        vector_docs = 0

    return JSONResponse(
        content={
            "n8n_sites": _read_n8n_sites(),
            "last_sync": _read_last_sync(),
            "vector_docs": vector_docs,
            "status": "online",
        }
    )
