import os

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from lib.rag import reload_vector_store

router = APIRouter()


@router.post("")
async def reindex(
    x_webhook_secret: str | None = Header(default=None, alias="x-webhook-secret"),
):
    expected = os.getenv("N8N_WEBHOOK_SECRET")
    if not expected or x_webhook_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    result = await reload_vector_store()
    return JSONResponse(
        content={
            "status": "reindexed",
            "timestamp": result["timestamp"],
            "count": result["reindexed"],
        }
    )
