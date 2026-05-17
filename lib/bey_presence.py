"""Beyond Presence (api.bey.dev) helpers."""

import os

import httpx

from lib.elevenlabs_convai import NILA_ELEVEN_PROMPT

BEY_API_BASE_DEFAULT = "https://api.bey.dev"
DEFAULT_AVATAR_ID = "694c83e2-8895-4a98-bd16-56332ca3f449"


def gemini_lip_sync_enabled() -> bool:
    """When true, publish Gemini audio into LiveKit for Bey speech-to-video lip-sync."""
    return (os.getenv("BEY_GEMINI_LIP_SYNC") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def api_base() -> str:
    return (os.getenv("BEYOND_PRESENCE_API_BASE") or BEY_API_BASE_DEFAULT).rstrip("/")


def api_key() -> str:
    key = (os.getenv("BEYOND_PRESENCE_API_KEY") or "").strip()
    if not key:
        raise ValueError("BEYOND_PRESENCE_API_KEY is not set")
    return key


def configured_agent_id() -> str:
    return (os.getenv("BEY_AGENT_ID") or os.getenv("BP_PERSONA_ID") or "").strip()


def public_llm_base() -> str:
    return (os.getenv("NILA_PUBLIC_BASE_URL") or "").strip().rstrip("/")


async def public_llm_is_reachable() -> bool:
    """True when Bey (or this host) can reach the public RAG LLM URL."""
    base = public_llm_base()
    if not base:
        return False
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(f"{base}/api/status")
            if response.status_code < 500:
                return True
            response = await client.get(f"{base}/api/avatar/live/status")
            return response.status_code < 500
    except Exception:
        return False


async def ensure_default_bey_llm(client: httpx.AsyncClient, agent_id: str) -> dict:
    """Point agent at Bey-hosted OpenAI LLM (works without a public tunnel)."""
    model = (os.getenv("BEY_AGENT_LLM_MODEL") or "gpt-4.1-mini").strip()
    response = await client.patch(
        f"{api_base()}/v1/agents/{agent_id}",
        headers=bey_headers(),
        json={
            "llm": {
                "type": "openai",
                "model": model,
                "temperature": 0.3,
            },
        },
        timeout=60.0,
    )
    if response.status_code not in (200, 204):
        raise http_error(response, "Could not restore Bey default LLM on agent")
    return {
        "rag_enabled": False,
        "message": (
            "Avatar voice uses Bey’s LLM (local). Resources still load from Supabase "
            "when you ask government questions."
        ),
    }


def llm_api_secret() -> str:
    return (os.getenv("BEY_LLM_API_SECRET") or "nila-bey-llm").strip()


def openai_llm_url() -> str:
    """Base URL registered with Bey (must end with /v1)."""
    base = public_llm_base()
    if not base:
        return ""
    return f"{base}/api/avatar/openai/v1"


def rag_llm_configured() -> bool:
    return bool(public_llm_base() and (os.getenv("BEY_EXTERNAL_LLM_API_ID") or "").strip())


async def external_llm_url_matches() -> bool:
    """True when Bey's registered external LLM URL matches NILA_PUBLIC_BASE_URL."""
    expected = openai_llm_url()
    if not expected:
        return False
    try:
        async with httpx.AsyncClient() as client:
            apis = await list_external_apis(client)
    except Exception:
        return False
    name = os.getenv("BEY_EXTERNAL_LLM_NAME", "Nila Supabase RAG")
    for item in apis:
        if not isinstance(item, dict):
            continue
        if item.get("name") == name or _urls_match(item.get("url", ""), expected):
            return _urls_match(item.get("url", ""), expected)
    return False


def bey_headers() -> dict[str, str]:
    return {"x-api-key": api_key(), "Content-Type": "application/json"}


def http_error(response: httpx.Response, context: str) -> ValueError:
    try:
        body = response.json()
        detail = body.get("detail", body)
    except Exception:
        detail = response.text[:300]
    if response.status_code == 404:
        return ValueError(
            f"{context}: agent not found. Run POST /api/avatar/setup to create a Nila "
            f"agent, or set BEY_AGENT_ID from GET /api/avatar/agents. Detail: {detail}"
        )
    if response.status_code == 401:
        return ValueError(
            f"{context}: invalid API key. Create one at https://app.bey.chat/settings"
        )
    return ValueError(f"{context}: HTTP {response.status_code} — {detail}")


async def list_agents(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(f"{api_base()}/v1/agents", headers=bey_headers())
    if not response.is_success:
        raise http_error(response, "Could not list agents")
    data = response.json()
    if isinstance(data, list):
        return data
    return data.get("data") or []


async def list_external_apis(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(f"{api_base()}/v1/external-apis", headers=bey_headers())
    if not response.is_success:
        raise http_error(response, "Could not list external APIs")
    data = response.json()
    if isinstance(data, list):
        return data
    return data.get("data") or []


async def retrieve_external_api(client: httpx.AsyncClient, api_id: str) -> dict | None:
    response = await client.get(
        f"{api_base()}/v1/external-apis/{api_id}",
        headers=bey_headers(),
        timeout=30.0,
    )
    if not response.is_success:
        return None
    return response.json()


async def delete_external_api(client: httpx.AsyncClient, api_id: str) -> None:
    response = await client.delete(
        f"{api_base()}/v1/external-apis/{api_id}",
        headers=bey_headers(),
        timeout=30.0,
    )
    if response.status_code not in (200, 204, 404):
        raise http_error(response, "Could not delete stale external LLM registration")


async def _create_external_llm_api(client: httpx.AsyncClient, *, name: str, llm_url: str) -> str:
    response = await client.post(
        f"{api_base()}/v1/external-apis",
        headers=bey_headers(),
        json={
            "type": "openai_compatible_llm",
            "name": name,
            "url": llm_url,
            "api_key": llm_api_secret(),
        },
        timeout=60.0,
    )
    if not response.is_success:
        raise http_error(response, "Could not register external LLM with Beyond Presence")
    return response.json()["id"]


def _urls_match(a: str, b: str) -> bool:
    return (a or "").rstrip("/") == (b or "").rstrip("/")


async def ensure_external_llm_api(client: httpx.AsyncClient) -> str:
    """
    Register Nila OpenAI-compatible RAG endpoint with Bey; return api_id.

    Cloudflare/ngrok URLs change often — if a registered URL no longer matches
    NILA_PUBLIC_BASE_URL, create a fresh registration and remove stale ones.
    """
    llm_url = openai_llm_url()
    if not llm_url:
        raise ValueError(
            "NILA_PUBLIC_BASE_URL is not set. Bey must reach your API for Supabase RAG "
            "(use ngrok in dev: https://xxxx.ngrok-free.app)."
        )

    name = os.getenv("BEY_EXTERNAL_LLM_NAME", "Nila Supabase RAG")
    apis = await list_external_apis(client)

    for item in apis:
        if isinstance(item, dict) and _urls_match(item.get("url", ""), llm_url):
            return item["id"]

    env_id = (os.getenv("BEY_EXTERNAL_LLM_API_ID") or "").strip()
    if env_id:
        current = await retrieve_external_api(client, env_id)
        if current and _urls_match(current.get("url", ""), llm_url):
            return env_id

    stale_ids: list[str] = []
    for item in apis:
        if not isinstance(item, dict):
            continue
        item_id = (item.get("id") or "").strip()
        if not item_id:
            continue
        if item.get("name") == name and not _urls_match(item.get("url", ""), llm_url):
            stale_ids.append(item_id)
    if env_id and env_id not in stale_ids:
        current = await retrieve_external_api(client, env_id)
        if current and not _urls_match(current.get("url", ""), llm_url):
            stale_ids.append(env_id)

    new_id = await _create_external_llm_api(client, name=name, llm_url=llm_url)

    for old_id in stale_ids:
        if old_id == new_id:
            continue
        try:
            await delete_external_api(client, old_id)
        except ValueError:
            pass

    return new_id


async def ensure_rag_agent(client: httpx.AsyncClient, agent_id: str) -> dict:
    """Point agent LLM at Nila RAG (OpenAI-compatible). Returns status dict."""
    if not public_llm_base():
        return await ensure_default_bey_llm(client, agent_id)

    if not await public_llm_is_reachable():
        return {
            **await ensure_default_bey_llm(client, agent_id),
            "message": (
                "NILA_PUBLIC_BASE_URL is set but not reachable (tunnel down?). "
                "Avatar voice uses Bey’s LLM for now. Start "
                "./scripts/start-public-tunnel.sh, update .env, then POST /api/avatar/setup."
            ),
        }

    api_id = await ensure_external_llm_api(client)
    voice_prompt = os.getenv(
        "BEY_RAG_VOICE_SYSTEM_PROMPT",
        "You are Nila, a Sri Lanka government services voice assistant. "
        "Speak in clear English. Read aloud the assistant text from the language model "
        "exactly — do not add facts or refuse content the model already provided.",
    )
    patch = {
        "language": "en",
        "system_prompt": voice_prompt,
        "llm": {
            "type": "openai_compatible",
            "api_id": api_id,
            "model": os.getenv("BEY_EXTERNAL_LLM_MODEL", "nila-rag"),
            "temperature": 0.3,
        },
    }
    response = await client.patch(
        f"{api_base()}/v1/agents/{agent_id}",
        headers=bey_headers(),
        json=patch,
        timeout=60.0,
    )
    if response.status_code not in (200, 204):
        raise http_error(response, "Could not update agent for RAG LLM")

    return {
        "rag_enabled": True,
        "external_api_id": api_id,
        "openai_llm_url": openai_llm_url(),
        "message": "Agent uses Nila Supabase RAG via external LLM.",
    }


async def list_avatars(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(f"{api_base()}/v1/avatars", headers=bey_headers())
    if not response.is_success:
        raise http_error(response, "Could not list avatars")
    data = response.json()
    if isinstance(data, list):
        return data
    return data.get("data") or []


async def create_nila_agent(client: httpx.AsyncClient) -> dict:
    avatar_id = (os.getenv("BEY_AVATAR_ID") or DEFAULT_AVATAR_ID).strip()
    model = (os.getenv("BEY_AGENT_LLM_MODEL") or "gpt-4.1-mini").strip()
    payload: dict = {
        "name": os.getenv("BEY_AGENT_NAME", "Nila"),
        "avatar_id": avatar_id,
        "system_prompt": os.getenv("BEY_AGENT_SYSTEM_PROMPT", NILA_ELEVEN_PROMPT),
        "language": "en",
        "greeting": (
            "Hello! I'm Nila, your Sri Lanka government services assistant. "
            "How can I help you today?"
        ),
        "llm": {
            "type": "openai",
            "model": model,
            "temperature": 0.3,
        },
    }
    if public_llm_base():
        api_id = await ensure_external_llm_api(client)
        payload["llm"] = {
            "type": "openai_compatible",
            "api_id": api_id,
            "model": os.getenv("BEY_EXTERNAL_LLM_MODEL", "nila-rag"),
            "temperature": 0.3,
        }

    response = await client.post(
        f"{api_base()}/v1/agents",
        headers=bey_headers(),
        json=payload,
        timeout=60.0,
    )
    if not response.is_success:
        raise http_error(response, "Could not create Beyond Presence agent")
    return response.json()


async def create_call(client: httpx.AsyncClient, agent_id: str) -> dict:
    response = await client.post(
        f"{api_base()}/v1/calls",
        headers=bey_headers(),
        json={"agent_id": agent_id},
        timeout=120.0,
    )
    if not response.is_success:
        raise http_error(response, "Could not create Beyond Presence call")
    return response.json()


async def resolve_avatar_id(client: httpx.AsyncClient, agent_id: str) -> str:
    """Avatar model id for speech-to-video (distinct from conversational agent id)."""
    env_avatar = (os.getenv("BEY_AVATAR_ID") or "").strip()
    if env_avatar:
        return env_avatar
    response = await client.get(
        f"{api_base()}/v1/agents/{agent_id}",
        headers=bey_headers(),
        timeout=30.0,
    )
    if response.is_success:
        data = response.json()
        aid = (data.get("avatar_id") or "").strip()
        if aid:
            return aid
    return DEFAULT_AVATAR_ID


async def create_speech_to_video_session(
    client: httpx.AsyncClient,
    *,
    avatar_id: str,
    livekit_url: str,
    livekit_token: str,
) -> dict:
    """Start Bey speech-to-video worker in an existing LiveKit room."""
    response = await client.post(
        f"{api_base()}/v1/sessions",
        headers=bey_headers(),
        json={
            "transport": "livekit",
            "avatar_id": avatar_id,
            "url": livekit_url,
            "token": livekit_token,
        },
        timeout=120.0,
    )
    if not response.is_success:
        raise http_error(response, "Could not create Beyond Presence speech-to-video session")
    return response.json()


async def bey_gemini_livekit_room() -> dict:
    """Provision LiveKit room/token only (browser should connect before speech-to-video)."""
    setup = await ensure_setup()
    agent_id = setup["agent_id"]
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        call = await create_call(client, agent_id)
        livekit_url = call.get("livekit_url") or ""
        livekit_token = call.get("livekit_token") or ""
        if not livekit_url or not livekit_token:
            raise ValueError("Beyond Presence call missing livekit_url/livekit_token")
        avatar_id = await resolve_avatar_id(client, agent_id)

    lip_sync = gemini_lip_sync_enabled()
    return {
        "beyond_presence": True,
        "agent_id": agent_id,
        "avatar_id": avatar_id,
        "livekit_url": livekit_url,
        "livekit_token": livekit_token,
        "call_id": call.get("id"),
        "video_mode": "lip_sync" if lip_sync else "agent",
        "lip_sync_mode": "speech_to_video" if lip_sync else "agent",
        "embed_url": setup.get("embed_url") or f"https://bey.chat/{agent_id}",
    }


async def bey_gemini_start_speech_to_video(room: dict) -> dict:
    """Start Bey lip-sync worker after the browser has joined the LiveKit room."""
    livekit_url = room.get("livekit_url") or ""
    livekit_token = room.get("livekit_token") or ""
    avatar_id = room.get("avatar_id") or ""
    if not livekit_url or not livekit_token or not avatar_id:
        raise ValueError("Missing LiveKit room credentials for speech-to-video")

    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        s2v = await create_speech_to_video_session(
            client,
            avatar_id=avatar_id,
            livekit_url=livekit_url,
            livekit_token=livekit_token,
        )
    s2v_id = s2v.get("id") or ""
    return {**room, "speech_to_video_session_id": s2v_id, "speech_to_video_ok": bool(s2v_id)}


async def bey_gemini_livekit_bundle(*, wait_for_client: bool = False) -> dict:
    """
    LiveKit room + speech-to-video for Gemini voice with lip-synced face.

    When wait_for_client is False, speech-to-video starts immediately (legacy).
    Avatar UI should call bey_gemini_start_speech_to_video after room.connect().
    """
    room = await bey_gemini_livekit_room()
    if wait_for_client:
        return room

    s2v_warning = ""
    try:
        room = await bey_gemini_start_speech_to_video(room)
    except Exception as exc:
        s2v_warning = str(exc)

    if s2v_warning:
        room["speech_to_video_warning"] = s2v_warning
    return room


async def resolve_agent_id(client: httpx.AsyncClient) -> str:
    """Use env agent if valid, else first agent in account, else create Nila."""
    env_id = configured_agent_id()
    agents = await list_agents(client)
    if env_id:
        for item in agents:
            if item.get("id") == env_id:
                return env_id
    if agents:
        return agents[0]["id"]
    created = await create_nila_agent(client)
    return created["id"]


async def ensure_setup() -> dict:
    async with httpx.AsyncClient() as client:
        agents_before = await list_agents(client)
        agent_id = await resolve_agent_id(client)
        agents_after = await list_agents(client)
        rag = await ensure_rag_agent(client, agent_id)
        return {
            "ok": True,
            "agent_id": agent_id,
            "embed_url": f"https://bey.chat/{agent_id}",
            "agents_count": len(agents_after),
            "created_new": len(agents_after) > len(agents_before),
            "rag_enabled": rag.get("rag_enabled", False),
            "openai_llm_url": openai_llm_url() or None,
            "rag_message": rag.get("message"),
        }
