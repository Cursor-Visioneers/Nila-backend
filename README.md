# Nila Backend

FastAPI backend for **Nila**, the GIC (Government Information Center) AI assistant for Sri Lanka. Powers multilingual chat (English, Sinhala, Tamil), RAG over government knowledge, structured resource extraction (forms, offices, laws), avatar voice/streaming via ElevenLabs + Beyond Presence, and n8n content ingestion pipelines.

**Base URL (local):** `http://localhost:8000`  
**Interactive API docs:** [http://localhost:8000/docs](http://localhost:8000/docs)  
**OpenAPI JSON:** [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

---

## Table of contents

1. [Architecture overview](#architecture-overview)
2. [Tech stack](#tech-stack)
3. [Project structure](#project-structure)
4. [Environment variables](#environment-variables)
5. [Supabase setup](#supabase-setup)
6. [Run locally](#run-locally)
7. [Content & seeding](#content--seeding)
8. [API reference](#api-reference)
9. [Frontend integration guide](#frontend-integration-guide)
10. [Internal / n8n endpoints](#internal--n8n-endpoints)
11. [Core library modules](#core-library-modules)
12. [Language detection](#language-detection)
13. [Chat pipeline (detailed)](#chat-pipeline-detailed)
14. [Resource extraction](#resource-extraction)
15. [CORS & errors](#cors--errors)
16. [Troubleshooting](#troubleshooting)

---

## Architecture overview

```
┌──────────────┐     POST /api/chat      ┌─────────────────────────────────────┐
│   Frontend   │ ───────────────────────►│  FastAPI (main.py)                  │
│   (React)    │                         │  ├─ language_detector               │
└──────┬───────┘                         │  ├─ RAG: search_knowledge (Supabase)│
       │                                 │  ├─ LLM: OpenAI (en/ta) or Gemini(si)│
       │  reply, resources, session_id   │  └─ resource_extractor              │
       ◄─────────────────────────────────┤                                     │
       │                                 └─────────────────────────────────────┘
       │ POST /api/avatar
       ▼
┌──────────────┐   ElevenLabs TTS    ┌──────────────────┐   WebRTC URL
│ Avatar player│ ◄────────────────── │ Beyond Presence  │ ◄──────────────
└──────────────┘                     └──────────────────┘

┌──────────────┐     POST /api/n8n/convert     ┌──────────────┐
│     n8n      │ ─────────────────────────────►│ OpenAI +     │
│  (scraping)  │                               │ Gemini + RAG │
└──────────────┘     POST /api/reindex          └──────────────┘
```

| Layer | Technology |
|-------|------------|
| API framework | FastAPI + Uvicorn |
| Vector DB | Supabase (PostgreSQL + pgvector) |
| Embeddings | OpenAI `text-embedding-3-small` (1536 dims) |
| Chat (EN/TA) | OpenAI `gpt-4o` |
| Chat (SI) | Google Gemini `gemini-1.5-pro` |
| TTS | ElevenLabs `eleven_multilingual_v2` |
| Avatar stream | Beyond Presence WebRTC API |
| Language detect | Unicode blocks + `langdetect` |

---

## Tech stack

Dependencies (`requirements.txt`):

| Package | Purpose |
|---------|---------|
| `fastapi` | HTTP API |
| `uvicorn` | ASGI server |
| `python-dotenv` | Load `.env` |
| `openai` | Embeddings + GPT-4o chat |
| `google-generativeai` | Sinhala chat + Sinhala Markdown translation |
| `supabase` | Vector store client |
| `httpx` | Async HTTP (ElevenLabs, Beyond Presence) |
| `langdetect` | Language fallback detection |
| `pydantic` | Request/response models |
| `python-multipart` | Form uploads (future) |
| `sse-starlette` | SSE streaming (future) |

---

## Project structure

```
nila-backend/
├── main.py                 # FastAPI app, CORS, router mounts, GET /
├── seed_content.py         # CLI: seed Supabase from content/**/*.md
├── requirements.txt
├── .env.example
├── README.md
│
├── routers/
│   ├── chat.py             # POST /api/chat — main chat + RAG
│   ├── avatar.py           # POST /api/avatar — TTS + BP stream URL
│   ├── status.py           # GET /api/status — dashboard badges
│   ├── reindex.py          # POST /api/reindex — webhook reindex
│   ├── n8n_convert.py      # POST /api/n8n/convert — HTML → Markdown
│   └── resources.py        # Stub (not implemented)
│
├── lib/
│   ├── rag.py              # Embeddings, search, upsert, reload, count
│   ├── openai_client.py    # GPT-4o responses (EN/TA)
│   ├── gemini_client.py    # Gemini Sinhala responses
│   ├── language_detector.py# Unicode + langdetect
│   └── resource_extractor.py # Parse RESOURCES / RESOURCE: / සම්පත්:
│
└── content/
    ├── en/                 # English Markdown knowledge files
    ├── si/                 # Sinhala Markdown
    ├── ta/                 # Tamil Markdown
    └── synced/             # n8n sync metadata (optional)
        ├── n8n_sites.json  # {"n8n_sites": 312}
        └── synced_at.txt   # ISO timestamp of last sync
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in all required keys:

```bash
cp .env.example .env
```

| Variable | Required by | Description |
|----------|-------------|-------------|
| `OPENAI_API_KEY` | RAG, chat (en/ta), n8n convert | OpenAI API key for embeddings and GPT-4o |
| `GEMINI_API_KEY` | Chat (si), n8n Sinhala translate | Google AI Studio / Gemini API key |
| `SUPABASE_URL` | RAG, status, seed | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | RAG, status, seed | Service role key (server-side only) |
| `ELEVENLABS_API_KEY` | Avatar | ElevenLabs TTS |
| `BEYOND_PRESENCE_API_KEY` | Avatar | Beyond Presence Bearer token |
| `BP_PERSONA_ID` | Avatar | Beyond Presence persona ID |
| `VOICE_ID_EN` | Avatar | ElevenLabs voice ID for English |
| `VOICE_ID_SI` | Avatar | ElevenLabs voice ID for Sinhala |
| `VOICE_ID_TA` | Avatar | ElevenLabs voice ID for Tamil |
| `N8N_WEBHOOK_SECRET` | Reindex | Shared secret for `x-webhook-secret` header |
| `ADMIN_USER` | (reserved) | Default `admin` — future admin routes |
| `ADMIN_PASS` | (reserved) | Default `nila2025` — future admin routes |

**Security:** Never expose `SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY`, or webhook secrets to the browser. Frontend calls only this backend; keys stay server-side.

---

## Supabase setup

Run in the **Supabase SQL editor** before seeding or chatting:

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
    d.id,
    d.content,
    d.metadata,
    d.source_url,
    d.dept,
    1 - (d.embedding <=> query_embedding) AS similarity
  FROM documents d
  WHERE (filter_language IS NULL OR d.language = filter_language)
  ORDER BY d.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
```

### Documents table

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid | Primary key |
| `content` | text | Full Markdown document (embedded) |
| `metadata` | jsonb | Title, path, frontmatter fields, etc. |
| `embedding` | vector(1536) | OpenAI `text-embedding-3-small` |
| `language` | text | `en`, `si`, or `ta` |
| `source_url` | text | Canonical government URL |
| `dept` | text | Department / service key (upsert uniqueness with `language`) |

**Upsert rule:** One row per `(dept, language)` pair — re-seeding or n8n convert updates existing rows instead of duplicating.

---

## Run locally

### 1. Virtual environment (recommended)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys and Supabase credentials
```

### 3. Set up Supabase

Run the [SQL above](#supabase-setup) in your Supabase project.

### 4. Seed the vector store

```bash
python seed_content.py
```

Expected output:

```
Seeded: birth-certificate.md (en)
Done. Seeded 1 file(s).
```

### 5. Start the API

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 6. Verify

```bash
curl http://localhost:8000/
# {"status":"Nila backend online","version":"1.0"}
```

---

## Content & seeding

### Content directories

| Path | Purpose |
|------|---------|
| `content/en/` | English government service Markdown |
| `content/si/` | Sinhala Markdown |
| `content/ta/` | Tamil Markdown |
| `content/synced/` | Ops metadata (`n8n_sites.json`, `synced_at.txt`) |

### Markdown frontmatter (recommended)

```yaml
---
service_id: birth-cert-en
dept: RegistrarGeneral
source_url: https://www.registrar-general.gov.lk
language: en
---
```

### Sample file

`content/en/birth-certificate.md` — birth certificate application (documents, fees, DS offices, forms, RESOURCES block).

### `seed_content.py`

Reads all `content/**/*.md` recursively and upserts into Supabase:

| Field | Source |
|-------|--------|
| `language` | Folder: `en/`, `si/`, or `ta/` (default `en`) |
| `dept` | Filename stem (e.g. `birth-certificate`) |
| `source_url` | Frontmatter `source_url`, else relative path |
| `content` | Full file text (including frontmatter) |
| `metadata` | Frontmatter fields + `filename`, `path` |

```bash
python seed_content.py
# Prints: Seeded: birth-certificate.md (en)
```

### `POST /api/reindex` (alternative to seed script)

Re-indexes all `content/**/*.md` via `reload_vector_store()` — same vector upsert logic as seed, triggered by n8n with webhook secret.

---

## API reference

All paths are relative to the server root. CORS is enabled for all origins (buildathon mode).

### Summary table

| Method | Path | Auth | Used by | Status |
|--------|------|------|---------|--------|
| `GET` | `/` | None | Health checks | ✅ |
| `POST` | `/api/chat` | None | **Frontend** | ✅ |
| `POST` | `/api/avatar` | None | **Frontend** | ✅ |
| `GET` | `/api/status` | None | **Frontend** | ✅ |
| `POST` | `/api/reindex` | `x-webhook-secret` | n8n / ops | ✅ |
| `POST` | `/api/n8n/convert` | None | n8n | ✅ |
| `GET` | `/api/resources/` | None | — | ⚠️ Stub only |
| `OPTIONS` | `/api/chat`, `/api/avatar` | None | CORS preflight | ✅ |

---

### `GET /` — Health check

**Purpose:** Confirm API is running.

**Response `200`:**

```json
{
  "status": "Nila backend online",
  "version": "1.0"
}
```

---

### `POST /api/chat` — Main chat endpoint

**Purpose:** User message → RAG retrieval → LLM answer → structured resources for Resource Box panel.

**Headers:**

```
Content-Type: application/json
```

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | string | *required* | User's current message |
| `language` | string | `"auto"` | `"auto"`, `"en"`, `"si"`, or `"ta"` |
| `history` | array | `[]` | Prior turns: `{"role": "user"\|"assistant", "content": "..."}` |
| `session_id` | string \| null | `null` | Pass previous `session_id` to continue session |

**Example request:**

```json
{
  "message": "How do I apply for a birth certificate?",
  "language": "auto",
  "history": [
    { "role": "user", "content": "Hello" },
    { "role": "assistant", "content": "Welcome to Nila..." }
  ],
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Success response `200`:**

| Field | Type | Description |
|-------|------|-------------|
| `reply` | string | Cleaned answer text (no raw `RESOURCES:` block) |
| `engine` | `"openai"` \| `"gemini"` | Which LLM was used |
| `language` | `"en"` \| `"si"` \| `"ta"` | Resolved language |
| `resources` | array | Structured forms/offices/laws for side panel |
| `session_id` | string | UUID — store and resend on next request |

**Example success:**

```json
{
  "reply": "To apply for a birth certificate...\n\nYou can find the relevant forms and office details in the panel on the right.",
  "engine": "openai",
  "language": "en",
  "resources": [
    {
      "type": "form",
      "name": "BDR-1 Birth Registration Form",
      "url": "https://www.registrar-general.gov.lk/forms",
      "label": "Download Form"
    },
    {
      "type": "office",
      "name": "Divisional Secretariat (nearest)",
      "url": "https://www.dsboffice.gov.lk",
      "label": "Visit Office"
    },
    {
      "type": "law",
      "name": "Births and Deaths Registration Act No. 17 of 1951",
      "url": "https://www.registrar-general.gov.lk",
      "label": "View Law"
    }
  ],
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Resource object:**

| Field | Type | Values |
|-------|------|--------|
| `type` | string | `"form"`, `"office"`, `"law"` |
| `name` | string | Display name |
| `url` | string \| null | Link or address text |
| `label` | string | `"Download Form"`, `"Visit Office"`, `"View Law"` |

**Error response `500`:**

```json
{ "error": "OPENAI_API_KEY is not set" }
```

**Reply footer (when resources exist):**

| Language | Appended sentence |
|----------|-------------------|
| `en` | You can find the relevant forms and office details in the panel on the right. |
| `si` | අදාළ ලේඛන සහ කාර්යාල තොරතුරු දකුණු පැත්තේ පෙනෙනු ඇත. |
| `ta` | சம்பந்தப்பட்ட படிவங்கள் மற்றும் அலுவலக விவரங்களை வலதுபுற panel-இல் காணலாம். |

**`OPTIONS /api/chat`:** CORS preflight (empty body).

---

### `POST /api/avatar` — Avatar stream URL

**Purpose:** Convert reply text to speech (ElevenLabs) and start Beyond Presence WebRTC stream.

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | *required* | Text to speak (usually `reply` from chat) |
| `language` | string | `"en"` | `"en"`, `"si"`, or `"ta"` — selects voice ID |

**Example request:**

```json
{
  "text": "To apply for a birth certificate, visit your Divisional Secretariat.",
  "language": "en"
}
```

**Success response `200`:**

```json
{
  "stream_url": "https://api.beyondpresence.ai/...",
  "audio_generated": true
}
```

**Error response `200` (check body, not HTTP status):**

```json
{
  "stream_url": null,
  "audio_generated": false,
  "error": "ELEVENLABS_API_KEY is not set"
}
```

**Pipeline:**

1. `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}` — model `eleven_multilingual_v2`
2. Base64-encode audio
3. `POST https://api.beyondpresence.ai/v1/stream` — `persona_id`, `audio`, `format: "webrtc"`
4. Return `stream_url` from response

**Voice ID mapping:**

| `language` | Env variable |
|------------|--------------|
| `en` | `VOICE_ID_EN` |
| `si` | `VOICE_ID_SI` |
| `ta` | `VOICE_ID_TA` |

**`OPTIONS /api/avatar`:** CORS preflight.

---

### `GET /api/status` — Dashboard / n8n badge

**Purpose:** Sync stats for UI badges (sites indexed, last sync, vector doc count).

**Response `200`:**

```json
{
  "n8n_sites": 312,
  "last_sync": "2026-05-16T10:30:00+00:00",
  "vector_docs": 15,
  "status": "online"
}
```

| Field | Source |
|-------|--------|
| `n8n_sites` | `content/synced/n8n_sites.json` → key `n8n_sites`, else **312** (demo default) |
| `last_sync` | `content/synced/synced_at.txt` ISO string, or **`"never"`** |
| `vector_docs` | `COUNT(*)` on Supabase `documents` table |
| `status` | Always `"online"` when endpoint responds |

**Optional `content/synced/n8n_sites.json`:**

```json
{ "n8n_sites": 312 }
```

**Optional `content/synced/synced_at.txt`:**

```
2026-05-16T10:30:00+00:00
```

---

### `POST /api/reindex` — Reindex vector store (webhook)

**Purpose:** Reload all `content/**/*.md` into Supabase. Called by n8n after bulk content updates.

**Headers:**

```
x-webhook-secret: <value of N8N_WEBHOOK_SECRET>
```

**Success `200`:**

```json
{
  "status": "reindexed",
  "timestamp": "2026-05-16T12:00:00+00:00",
  "count": 4
}
```

**Error `401`:**

```json
{ "detail": "Invalid webhook secret" }
```

**Not for public frontend** — server-to-server only.

---

### `POST /api/n8n/convert` — HTML to Markdown (n8n pipeline)

**Purpose:** Convert scraped government HTML to Markdown, optionally translate to Sinhala, upsert into vector DB.

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | string | *required* | Page title |
| `content` | string | *required* | Raw HTML or text |
| `dept` | string | *required* | Department / service ID |
| `url` | string | *required* | Source URL |
| `language` | string | `"en"` | Trigger Sinhala translation if `"si"` or dept has SI hint |

**Example request:**

```json
{
  "title": "Birth Certificate",
  "content": "<html>...</html>",
  "dept": "RegistrarGeneral",
  "url": "https://www.registrar-general.gov.lk",
  "language": "en"
}
```

**Success `200`:**

```json
{
  "markdown": "---\nservice_id: RegistrarGeneral\n...",
  "dept": "RegistrarGeneral",
  "url": "https://www.registrar-general.gov.lk",
  "si_markdown": null
}
```

| Field | Description |
|-------|-------------|
| `markdown` | English Markdown with YAML frontmatter |
| `si_markdown` | Sinhala Markdown if translation ran, else `null` |

**Side effects:** Upserts EN (and SI if present) into Supabase `documents`.

---

### `GET /api/resources/` — Stub

**Status:** Not implemented.

**Response `200`:**

```json
{ "message": "Resources API" }
```

**Use `resources` from `POST /api/chat` instead** for the Resource Box UI.

---

## Frontend integration guide

### Endpoints the UI must use

| Priority | Endpoint | When |
|----------|----------|------|
| **Required** | `POST /api/chat` | Every user message |
| **Required** | `GET /api/status` | Dashboard badges / sync indicator |
| **If avatar** | `POST /api/avatar` | After chat reply, to drive WebRTC avatar |
| Optional | `GET /` | Health dot on load |

### Recommended chat flow

```
User types message
    → POST /api/chat { message, language, history, session_id }
    → Display reply in chat bubble
    → Render resources[] in Resource Box panel
    → Save session_id + append to history
    → (Optional) POST /api/avatar { text: reply, language }
    → Connect stream_url to Beyond Presence player
```

### JavaScript example

```javascript
const API = "http://localhost:8000";

// Chat
const chatRes = await fetch(`${API}/api/chat`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    message: userInput,
    language: selectedLang, // "auto" | "en" | "si" | "ta"
    history: conversationHistory,
    session_id: storedSessionId ?? null,
  }),
});
const chat = await chatRes.json();

if (!chatRes.ok || chat.error) {
  showError(chat.error ?? "Chat failed");
  return;
}

storedSessionId = chat.session_id;
conversationHistory.push({ role: "user", content: userInput });
conversationHistory.push({ role: "assistant", content: chat.reply });

renderMessage(chat.reply);
renderResourceBox(chat.resources); // type, name, url, label

// Avatar (optional)
const avatarRes = await fetch(`${API}/api/avatar`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text: chat.reply, language: chat.language }),
});
const avatar = await avatarRes.json();

if (avatar.audio_generated && avatar.stream_url) {
  startWebRTCPlayer(avatar.stream_url);
}

// Status badge (on load or interval)
const status = await fetch(`${API}/api/status`).then((r) => r.json());
updateBadge(`${status.n8n_sites} sites · ${status.vector_docs} docs`);
```

### Frontend checklist

- [ ] Send full `history` each turn (user + assistant messages)
- [ ] Persist `session_id` (localStorage / state)
- [ ] Map `resources` to Resource Box — use `label` for button text
- [ ] Handle chat `500` via `error` field
- [ ] Handle avatar failures via `audio_generated === false` (HTTP may still be 200)
- [ ] Do **not** call `/api/reindex` or `/api/n8n/convert` from the public UI
- [ ] Do **not** expect resources from `/api/resources` (stub)

---

## Internal / n8n endpoints

| Endpoint | Caller | Secret |
|----------|--------|--------|
| `POST /api/n8n/convert` | n8n scrape workflow | None (restrict by network in production) |
| `POST /api/reindex` | n8n post-sync | `x-webhook-secret: N8N_WEBHOOK_SECRET` |

**Suggested n8n flow:**

1. Scrape gov site → `POST /api/n8n/convert`
2. Write/sync Markdown to `content/` (optional)
3. Update `content/synced/synced_at.txt`
4. `POST /api/reindex` with webhook secret

---

## Core library modules

### `lib/rag.py`

| Function | Description |
|----------|-------------|
| `get_embedding(text)` | OpenAI `text-embedding-3-small` → 1536-dim vector |
| `search_knowledge(query, language, k=5)` | RPC `match_documents` on Supabase |
| `upsert_document(...)` | Embed + insert/update by `(dept, language)` |
| `reload_vector_store()` | Re-index all `content/**/*.md` |
| `count_documents()` | Row count for status endpoint |

### `lib/openai_client.py`

| Function | Model | Use |
|----------|-------|-----|
| `generate_response(message, context, history, language)` | `gpt-4o` | EN/TA chat |

System prompt includes `KNOWLEDGE:` context and instructs model to append structured `RESOURCES:` block.

### `lib/gemini_client.py`

| Function | Model | Use |
|----------|-------|-----|
| `generate_sinhala_response(message, context, history)` | `gemini-1.5-pro` | SI chat |

Uses `RESOURCE:` inline markers instead of `RESOURCES:` block format.

### `lib/language_detector.py`

| Function | Returns |
|----------|---------|
| `detect_language(text)` | `"en"`, `"si"`, or `"ta"` |

### `lib/resource_extractor.py`

| Function | Description |
|----------|-------------|
| `extract_resources(text)` | Parse resources → `[{type, name, url, label}]` |
| `strip_resources_from_reply(text)` | Remove resource markers from display text |

**Supported formats:**

- `RESOURCES:` section with `- FORM:`, `- OFFICE:`, `- LAW:` lines
- Sinhala `සම්පත්:` lines
- Inline `RESOURCE:` (Gemini)

---

## Language detection

Used when chat `language` is `"auto"`.

**Priority order:**

1. **Sinhala Unicode** (`\u0D80`–`\u0DFF`) → `"si"` (always; fixes langdetect on short Sinhala)
2. **Tamil Unicode** (`\u0B80`–`\u0BFF`) → `"ta"`
3. **langdetect** → map `si`/`ta`, else `"en"`
4. **On failure** → `"en"`

**LLM routing after detection:**

| Resolved language | Engine | Model |
|-----------------|--------|-------|
| `si` | `gemini` | `gemini-1.5-pro` |
| `en`, `ta` | `openai` | `gpt-4o` |

RAG search filters documents by resolved `language`.

---

## Chat pipeline (detailed)

```
1. Resolve language (auto-detect or explicit en/si/ta)
2. Generate session_id (uuid4) if not provided
3. search_knowledge(message, language, k=5)
4. Build context string with source_url + dept per chunk
5. If language == "si":
     generate_sinhala_response()  → Gemini
   Else:
     generate_response()          → OpenAI GPT-4o
6. extract_resources(raw_reply)
7. strip_resources_from_reply() + append panel footer if resources exist
8. Return { reply, engine, language, resources, session_id }
```

---

## Resource extraction

**OpenAI** is instructed to output:

```
RESOURCES:
- FORM: [name] | [url]
- OFFICE: [name] | [address or url]
- LAW: [name] | [url]
```

**Gemini (Sinhala)** may use `RESOURCE:` inline or `සම්පත්:` prefixes.

The backend parses all formats, returns structured `resources[]`, and strips markers from `reply` so the frontend only renders resources in the Resource Box panel.

---

## CORS & errors

### CORS

- **Global middleware** (`main.py`): `allow_origins=["*"]`, all methods/headers
- **Chat & avatar routes:** explicit `Access-Control-Allow-*` headers on responses

### Error patterns

| Endpoint | Success | Error |
|----------|---------|-------|
| `/api/chat` | 200 + body | 500 + `{"error": "..."}` |
| `/api/avatar` | 200 + `audio_generated: true` | 200 + `audio_generated: false` + `error` |
| `/api/reindex` | 200 | 401 `detail` |
| `/api/status` | 200 | `vector_docs: 0` if DB unreachable |

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---------|--------------|-----|
| `OPENAI_API_KEY is not set` | Missing `.env` | Set key, restart server |
| Empty chat answers | No seeded documents | Run `python seed_content.py` |
| `vector_docs: 0` on status | Supabase not configured / empty table | Run SQL + seed |
| Avatar `audio_generated: false` | ElevenLabs or BP keys/voices | Check `ELEVENLABS_API_KEY`, `VOICE_ID_*`, `BEYOND_PRESENCE_API_KEY` |
| Sinhala detected as English | Old detector | Ensure Unicode block rules in `language_detector.py` |
| Reindex 401 | Wrong webhook secret | Match `N8N_WEBHOOK_SECRET` header |
| CORS from browser | Wrong API URL | Point frontend to `http://localhost:8000` |

### Useful commands

```bash
# Health
curl http://localhost:8000/

# Chat
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"How do I get a birth certificate?","language":"en"}'

# Status
curl http://localhost:8000/api/status

# Reindex (replace SECRET)
curl -X POST http://localhost:8000/api/reindex \
  -H "x-webhook-secret: SECRET"
```

---

## License & contributors

GIC / Visioneers buildathon project. For questions about API contracts, see [Frontend integration guide](#frontend-integration-guide) or open an issue in the repository.
