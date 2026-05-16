from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import avatar, chat, n8n_convert, reindex, resources, status

load_dotenv()

app = FastAPI(title="Nila Backend", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(avatar.router, prefix="/api/avatar", tags=["avatar"])
app.include_router(reindex.router, prefix="/api/reindex", tags=["reindex"])
app.include_router(resources.router, prefix="/api/resources", tags=["resources"])
app.include_router(n8n_convert.router, prefix="/api/n8n", tags=["n8n"])
app.include_router(status.router, prefix="/api/status", tags=["status"])


@app.get("/")
def health_check():
    return {"status": "Nila backend online", "version": "1.0"}
