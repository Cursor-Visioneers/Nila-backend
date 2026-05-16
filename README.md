# Nila Backend

FastAPI backend for **Nila**, the GIC (Government Information Center) AI assistant for Sri Lanka. Powers multilingual chat (English, Sinhala, Tamil), **Supabase RAG** over government knowledge, structured resources (forms, offices, laws), and several **live voice** integrations.

| | |
|---|---|
| **Local API** | `http://localhost:8000` |
| **OpenAPI** | [http://localhost:8000/docs](http://localhost:8000/docs) |
| **Branch** | `cursor/fastapi-backend-scaffold` |

---

## Table of contents

1. [What you can run today](#what-you-can-run-today)
2. [Architecture](#architecture)
3. [Built-in test UIs](#built-in-test-uis)
4. [Project structure](#project-structure)
5. [Environment variables](#environment-variables)
6. [Run locally](#run-locally)
7. [Supabase setup](#supabase-setup)
8. [Avatar live (Beyond Presence + Supabase RAG)](#avatar-live-beyond-presence--supabase-rag)
9. [Live ElevenLabs (voice-only, full local RAG)](#live-elevenlabs-voice-only-full-local-rag)
10. [API reference (summary)](#api-reference-summary)
11. [Frontend integration](#frontend-integration)
12. [Content & seeding](#content--seeding)
13. [Chat pipeline](#chat-pipeline)
14. [Troubleshooting](#troubleshooting)

---

## What you can run today

| Feature | Best for | Needs public URL? |
|---------|----------|-------------------|
| **`POST /api/chat`** | Text chat + resource panel | No |
| **`GET /live-eleven`** | English live voice, Supabase in speech | No |
| **`GET /avatar`** | Video avatar + live talk + resources | **Yes** for spoken Supabase answers* |
| **`POST /api/avatar/ask`** | One-shot RAG answer + TTS (no live duplex) | No |

\*Without a public URL, the avatar still talks (Bey’s LLM) and the **resources panel** can update from speech transcripts or typed questions. For **spoken** answers grounded in Supabase (like live-eleven), set `NILA_PUBLIC_BASE_URL` (ngrok or Cloudflare tunnel).

---

## Architecture

```
┌─────────────┐  POST /api/chat          ┌──────────────────────────────────────┐
│  Frontend   │ ───────────────────────►│ FastAPI                               │
│             │◄──────────────────────────│  run_chat → Supabase vector search    │
└──────┬──────┘   reply, resources       │  OpenAI (en/ta) / Gemini (si)         │
       │                                  └──────────────────────────────────────┘
       │
       │  Avatar live (recommended prod path)
       │  ┌─ WebSocket /api/avatar/live/ws  → status, resources, transcript
       │  └─ LiveKit (livekit_url + token)  → mic + avatar video/audio
       ▼
┌─────────────┐  STT / TTS / video         ┌──────────────────────────────────────┐
│   Browser   │ ◄──────────────────────────►│ Beyond Presence (api.bey.dev)       │
└─────────────┘                            │  Calls POST {PUBLIC_URL}/api/avatar/ │
                                           │  openai/v1/chat/completions → RAG    │
                                           └──────────────────────────────────────┘

┌─────────────┐  WebSocket /api/live/eleven/ws
│   Browser   │ ◄──────────────────────────► ElevenLabs ConvAI (bridged by backend)
└─────────────┘                            → Supabase RAG via contextual_update
```

| Layer | Technology |
|-------|------------|
| API | FastAPI + Uvicorn |
| Vector DB | Supabase (pgvector) |
| Embeddings | OpenAI `text-embedding-3-small` |
| Chat EN/TA | OpenAI GPT-4o |
| Chat SI | Google Gemini |
| Live voice (EN) | ElevenLabs Conversational AI |
| Live avatar | Beyond Presence + LiveKit |
| Avatar RAG (voice) | OpenAI-compatible endpoint on this API |

---

## Built-in test UIs

| URL | Description |
|-----|-------------|
| `/` | Health JSON |
| `/docs` | Swagger UI |
| `/chat` | Multi-turn chat UI |
| `/live-eleven` | **English live voice** — full Supabase RAG, no avatar |
| `/avatar` | **Avatar live** — WebSocket + LiveKit + resources |
| `/live-en` | OpenAI Realtime speech (experimental) |
| `/live` | Gemini Live speech |
| `/voice` | Turn-based voice agent |
| `/test` | Avatar/voice smoke tests |

---

## Project structure

```
Nila-backend/
├── main.py                      # App, routers, static UI routes
├── seed_content.py              # Seed Supabase from content/**/*.md
├── requirements.txt
├── .env.example
│
├── routers/
│   ├── chat.py                  # POST /api/chat
│   ├── avatar.py                # Avatar setup, ask, LiveKit session, TTS
│   ├── avatar_live.py           # Live avatar WS + OpenAI RAG for Bey
│   ├── live_elevenlabs.py       # /api/live/eleven/* — ConvAI bridge
│   ├── live_openai.py           # /api/live/en/ws
│   ├── live.py                  # Gemini live WS
│   ├── voice.py                 # Turn-based voice
│   ├── status.py                # Dashboard stats
│   ├── reindex.py               # Webhook reindex
│   └── n8n_convert.py           # HTML → Markdown pipeline
│
├── lib/
│   ├── rag.py                   # Embeddings + Supabase search
│   ├── chat_service.py          # run_chat() — shared RAG + LLM
│   ├── bey_presence.py          # api.bey.dev agents, calls, external LLM
│   ├── bey_call_poller.py       # Poll call transcripts → RAG (local mode)
│   ├── avatar_live_sessions.py  # Push resources to live WS clients
│   ├── openai_chat_stream.py    # SSE for Bey external LLM
│   ├── elevenlabs_convai.py     # ElevenLabs live helpers
│   └── ...
│
├── static/
│   ├── live-eleven.html         # Reference live voice UI
│   ├── avatar-beyond.html       # Reference avatar live UI
│   └── js/livekit-bey.js        # LiveKit + mic helpers
│
├── scripts/
│   ├── start-public-tunnel.sh   # cloudflared or ngrok → :8000
│   └── complete-voice-rag-setup.sh
│
├── frontend/nila-avatar/        # React reference (App.jsx)
├── docs/FRONTEND_LIVE_ELEVEN.md # Frontend guide for live-eleven
└── content/                     # Markdown knowledge (en/si/ta)
```

---

## Environment variables

Copy `.env.example` to `.env`. **Never commit `.env`.**

### Core (chat + RAG)

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | Embeddings + EN/TA chat |
| `GEMINI_API_KEY` | For Sinhala | Sinhala chat + n8n translate |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes | Service role key (server only) |

### ElevenLabs (TTS + live-eleven)

| Variable | Required | Description |
|----------|----------|-------------|
| `ELEVENLABS_API_KEY` | For voice | TTS + ConvAI live |
| `ELEVENLABS_AGENT_ID` | Optional | Reuse existing ConvAI agent |
| `VOICE_ID_EN` / `SI` / `TA` | For TTS | ElevenLabs voice IDs |

### Beyond Presence (avatar live)

| Variable | Required | Description |
|----------|----------|-------------|
| `BEYOND_PRESENCE_API_KEY` | For avatar | From [app.bey.chat/settings](https://app.bey.chat/settings) |
| `BEY_AGENT_ID` | Recommended | Agent UUID from setup or dashboard |
| `BEYOND_PRESENCE_API_BASE` | Default OK | `https://api.bey.dev` |
| `BEY_AVATAR_ID` | Optional | Public avatar id for new agents |
| `BEY_AGENT_NAME` | Optional | Default `Nila` |
| `BP_PERSONA_ID` | Legacy alias | Same as `BEY_AGENT_ID` (old name) |

### Spoken Supabase RAG on avatar (Bey → your API)

| Variable | Required | Description |
|----------|----------|-------------|
| `NILA_PUBLIC_BASE_URL` | For voice RAG | Public `https` URL to this API (ngrok / Cloudflare tunnel) |
| `BEY_LLM_API_SECRET` | For voice RAG | Bearer token Bey sends; default `nila-bey-llm` |
| `BEY_EXTERNAL_LLM_API_ID` | Optional | Reuse existing Bey external API registration |
| `BEY_EXTERNAL_LLM_MODEL` | Optional | Default `nila-rag` |

### Ops

| Variable | Description |
|----------|-------------|
| `N8N_WEBHOOK_SECRET` | `x-webhook-secret` for `/api/reindex` |

---

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum OPENAI, SUPABASE, BEYOND_PRESENCE for avatar

# Supabase: run SQL below, then:
python seed_content.py

uvicorn main:app --reload --port 8000
```

Verify:

```bash
curl http://localhost:8000/
curl http://localhost:8000/api/avatar/live/status
curl "http://localhost:8000/api/avatar/live/rag-test?query=birth+registration+Sri+Lanka"
```

---

## Supabase setup

Run in the Supabase SQL editor:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  content text,
  metadata jsonb,
  embedding vector(1536),
  language text DEFAULT 'en',
  source_url text,
  dept text
);

CREATE OR REPLACE FUNCTION match_documents(
  query_embedding vector(1536),
  match_count int,
  filter_language text DEFAULT NULL
)
RETURNS TABLE(
  id uuid,
  content text,
  metadata jsonb,
  source_url text,
  dept text,
  similarity float
)
LANGUAGE plpgsql AS $$
BEGIN
  RETURN QUERY
  SELECT
    d.id, d.content, d.metadata, d.source_url, d.dept,
    1 - (d.embedding <=> query_embedding) AS similarity
  FROM documents d
  WHERE (filter_language IS NULL OR d.language = filter_language)
  ORDER BY d.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
```

Seed:

```bash
python seed_content.py
```

---

## Avatar live (Beyond Presence + Supabase RAG)

Real-time **speech-to-speech** with a **video avatar**. Uses the same `run_chat()` Supabase pipeline as chat and live-eleven when voice RAG is enabled.

### Modes

| Mode | `NILA_PUBLIC_BASE_URL` | Avatar speech | Resources panel |
|------|------------------------|---------------|-----------------|
| **Local** | Not set | Bey built-in LLM | Transcript polling + typed `text` on WS |
| **Voice RAG** | Set (tunnel) | **Supabase-grounded** | Updates on each spoken turn |

### Backend setup (voice RAG)

**Terminal 1 — API**

```bash
uvicorn main:app --reload --port 8000
```

**Terminal 2 — public tunnel** (pick one)

```bash
# Cloudflare (no account)
./scripts/start-public-tunnel.sh

# ngrok (needs authtoken once)
./scripts/start-public-tunnel.sh ngrok
```

Copy the **https** URL into `.env`:

```env
NILA_PUBLIC_BASE_URL=https://xxxx.trycloudflare.com
BEY_LLM_API_SECRET=nila-bey-llm
```

Restart uvicorn, then:

```bash
./scripts/complete-voice-rag-setup.sh https://xxxx.trycloudflare.com
```

Confirm:

```bash
curl http://localhost:8000/api/avatar/live/status
# "voice_uses_supabase_rag": true
# "ready": true
```

Test RAG through the tunnel:

```bash
curl -X POST "$NILA_PUBLIC_BASE_URL/api/avatar/openai/v1/chat/completions" \
  -H "Authorization: Bearer nila-bey-llm" \
  -H "Content-Type: application/json" \
  -d '{"model":"nila-rag","stream":false,"messages":[{"role":"user","content":"How do I register a birth in Sri Lanka?"}]}'
```

### Avatar live API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/avatar/live/status` | Readiness + `voice_uses_supabase_rag` |
| `GET` | `/api/avatar/live/rag-test` | Supabase smoke test (no WS) |
| `POST` | `/api/avatar/setup` | Create/fix Bey agent + external LLM |
| `POST` | `/api/avatar/live/session` | LiveKit creds (REST alternative) |
| `WS` | `/api/avatar/live/ws` | Session + resources + `ready` with LiveKit |
| `POST` | `/api/avatar/openai/v1/chat/completions` | **Called by Bey only** — RAG voice |

**Setup body** (optional public URL without editing `.env` first):

```json
POST /api/avatar/setup
{ "public_base_url": "https://xxxx.trycloudflare.com" }
```

### WebSocket protocol (`/api/avatar/live/ws`)

**Server → client**

| `type` | Use |
|--------|-----|
| `ready` | `livekit_url`, `livekit_token`, `voice_uses_supabase_rag`, `local_mode` |
| `status` | Status line |
| `resources` | Resource panel (`form` / `office` / `law`) |
| `rag_search` / `rag_applied` | Supabase query completed |
| `text` | `role`: `user` \| `model`, transcript |
| `error` | Error message |

**Client → server**

```json
{ "type": "ping" }
{ "type": "text", "text": "How do I register a birth in Sri Lanka?" }
```

Do **not** send microphone audio on this socket — use LiveKit.

### LiveKit (browser)

After `ready`:

```ts
import { Room, RoomEvent } from "livekit-client";

const room = new Room({
  audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true },
});
await room.connect(ready.livekit_url, ready.livekit_token);
await room.localParticipant.setMicrophoneEnabled(true);
await room.startAudio();
room.on(RoomEvent.TrackSubscribed, (track) => {
  if (track.kind === "video" || track.kind === "audio") track.attach(videoEl);
});
```

Connect only after a **user click** (mic permission). See `static/js/livekit-bey.js` and `static/avatar-beyond.html`.

### Other avatar endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/avatar/ask` | RAG + ElevenLabs TTS + optional LiveKit |
| `POST` | `/api/avatar/livekit-session` | LiveKit only |
| `GET` | `/api/avatar/agents` | List Bey agents |
| `GET` | `/api/avatar/embed` | iframe URL `https://bey.chat/{id}` |

---

## Live ElevenLabs (voice-only, full local RAG)

No Beyond Presence. Full duplex English voice with Supabase answers — works on **localhost only**.

| | |
|---|---|
| **UI** | `http://localhost:8000/live-eleven` |
| **WebSocket** | `ws://localhost:8000/api/live/eleven/ws` |
| **Status** | `GET /api/live/eleven/status` |
| **Frontend guide** | [docs/FRONTEND_LIVE_ELEVEN.md](docs/FRONTEND_LIVE_ELEVEN.md) |

---

## API reference (summary)

| Method | Path | Frontend? |
|--------|------|-----------|
| `GET` | `/` | Health |
| `POST` | `/api/chat` | **Yes** — main text chat |
| `GET` | `/api/status` | Dashboard badges |
| `GET` | `/api/avatar/live/status` | Avatar live readiness |
| `WS` | `/api/avatar/live/ws` | **Yes** — avatar live |
| `POST` | `/api/avatar/setup` | Ops / first run |
| `POST` | `/api/avatar/ask` | One-shot RAG + voice |
| `WS` | `/api/live/eleven/ws` | Live English voice |
| `POST` | `/api/reindex` | n8n only (webhook secret) |
| `POST` | `/api/n8n/convert` | n8n pipeline |

Full request/response shapes: **Swagger** at `/docs` or sections below for chat.

### `POST /api/chat`

```json
// Request
{ "message": "...", "language": "auto", "history": [], "session_id": null }

// Response
{
  "reply": "...",
  "engine": "openai",
  "language": "en",
  "resources": [{ "type": "form", "name": "...", "url": "...", "label": "Download Form" }],
  "session_id": "uuid"
}
```

### Resource object

| Field | Values |
|-------|--------|
| `type` | `form`, `office`, `law` |
| `name` | Display name |
| `url` | Link (optional) |
| `label` | `Download Form`, `Visit Office`, `View Law` |

---

## Frontend integration

### Text chat app

1. `POST /api/chat` each turn with `history` + `session_id`
2. Render `reply` and `resources[]`
3. Optional: `GET /api/status` for badges

### Avatar live app (separate frontend)

1. `GET /api/avatar/live/status` → enable Connect if `ready`
2. User clicks **Connect** → `WebSocket` `/api/avatar/live/ws`
3. On `ready` → LiveKit + `setMicrophoneEnabled(true)`
4. On `resources` → update side panel
5. Do **not** call `/api/avatar/openai/...` from the browser

**Dev proxy (Vite):**

```ts
// vite.config.ts
server: { proxy: { "/api": "http://localhost:8000" } }
```

```env
VITE_NILA_API_URL=http://localhost:8000
```

### Avatar live app (Beyond Presence + resource box)

See **[docs/FRONTEND_AVATAR_LIVE.md](docs/FRONTEND_AVATAR_LIVE.md)** — full WebSocket protocol, resource box contract, LiveKit, and TypeScript types.

### Live ElevenLabs app

See [docs/FRONTEND_LIVE_ELEVEN.md](docs/FRONTEND_LIVE_ELEVEN.md).

---

## Content & seeding

| Path | Language |
|------|----------|
| `content/en/` | English |
| `content/si/` | Sinhala |
| `content/ta/` | Tamil |

```bash
python seed_content.py
# or POST /api/reindex with x-webhook-secret
```

Markdown can include a `RESOURCES:` block; the backend also extracts forms/offices from retrieved chunks.

---

## Chat pipeline

```
message → detect language → search_knowledge (Supabase, k=5)
        → build context → OpenAI or Gemini
        → extract_resources → reply + resources[] + session_id
```

Shared entry point: `lib/chat_service.py` → `run_chat()`.  
Avatar live and live-eleven use the same function with `voice_mode=True` (shorter spoken-style answers).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ready: false` on avatar live | Set `BEYOND_PRESENCE_API_KEY`, Supabase keys |
| `voice_uses_supabase_rag: false` | Set `NILA_PUBLIC_BASE_URL`, restart API, run `./scripts/complete-voice-rag-setup.sh` |
| Bey 404 agent | `POST /api/avatar/setup`, set `BEY_AGENT_ID` in `.env` |
| No mic prompt | Connect on **button click**; call `setMicrophoneEnabled(true)` |
| Resources empty | Ask a **government** question; check `rag-test` endpoint |
| Tunnel died | Restart tunnel; update `.env` URL; run setup again |
| ngrok auth error | `ngrok config add-authtoken ...` or use `./scripts/start-public-tunnel.sh` (Cloudflare) |
| Empty chat answers | Run `python seed_content.py` |
| Sinhala wrong language | Unicode rules in `lib/language_detector.py` |

### Useful commands

```bash
# Health
curl http://localhost:8000/

# Chat
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"How do I get a birth certificate?","language":"en"}'

# Avatar live status
curl http://localhost:8000/api/avatar/live/status

# Supabase RAG test
curl "http://localhost:8000/api/avatar/live/rag-test?query=birth+registration"

# Bey agent setup
curl -X POST http://localhost:8000/api/avatar/setup \
  -H "Content-Type: application/json" \
  -d '{"public_base_url":"https://YOUR-TUNNEL-URL"}'
```

---

## Security notes

- Keep `SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY`, `BEYOND_PRESENCE_API_KEY`, and `BEY_LLM_API_SECRET` on the server only.
- CORS is `*` for development; restrict in production.
- `/api/reindex` requires `x-webhook-secret`.

---

## License

GIC / Visioneers buildathon project. For API questions, use `/docs` or open an issue on GitHub.
