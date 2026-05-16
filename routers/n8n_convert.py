from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def n8n_root():
    return {"message": "N8N convert API"}
