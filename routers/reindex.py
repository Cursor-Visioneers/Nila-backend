from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def reindex_root():
    return {"message": "Reindex API"}
