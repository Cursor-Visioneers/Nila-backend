from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def status_root():
    return {"message": "Status API"}
