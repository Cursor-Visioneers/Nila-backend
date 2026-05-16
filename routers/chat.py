from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def chat_root():
    return {"message": "Chat API"}
