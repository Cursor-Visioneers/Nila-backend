# Nila Backend

FastAPI backend for the Nila multilingual assistant (chat, avatar, RAG, resources, and n8n workflows).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

## Run

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## Endpoints

| Path | Description |
|------|-------------|
| `GET /` | Health check |
| `/api/chat` | Chat and RAG |
| `/api/avatar` | Avatar / voice |
| `/api/reindex` | Content reindexing |
| `/api/resources` | Resource management |
| `/api/n8n` | n8n workflow helpers |
| `/api/status` | Service status |

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

## Project layout

```
nila-backend/
  main.py
  routers/          # API route modules
  lib/              # Clients and shared logic
  content/          # en, si, ta, synced content
  .env.example
  requirements.txt
```
