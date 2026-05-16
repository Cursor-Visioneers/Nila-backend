from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def resources_root():
    return {"message": "Resources API"}
