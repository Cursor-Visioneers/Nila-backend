"""Beyond Presence (api.bey.dev) helpers."""

import os

import httpx

from lib.elevenlabs_convai import NILA_ELEVEN_PROMPT

BEY_API_BASE_DEFAULT = "https://api.bey.dev"
DEFAULT_AVATAR_ID = "694c83e2-8895-4a98-bd16-56332ca3f449"


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


async def ensure_external_llm_api(client: httpx.AsyncClient) -> str:
    """Register Nila OpenAI-compatible RAG endpoint with Bey; return api_id."""
    llm_url = openai_llm_url()
    if not llm_url:
        raise ValueError(
            "NILA_PUBLIC_BASE_URL is not set. Bey must reach your API for Supabase RAG "
            "(use ngrok in dev: https://xxxx.ngrok-free.app)."
        )

    env_id = (os.getenv("BEY_EXTERNAL_LLM_API_ID") or "").strip()
    if env_id:
        return env_id

    name = os.getenv("BEY_EXTERNAL_LLM_NAME", "Nila Supabase RAG")
    for item in await list_external_apis(client):
        if not isinstance(item, dict):
            continue
        if item.get("url", "").rstrip("/") == llm_url.rstrip("/"):
            return item["id"]
        if item.get("name") == name:
            return item["id"]

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


async def ensure_rag_agent(client: httpx.AsyncClient, agent_id: str) -> dict:
    """Point agent LLM at Nila RAG (OpenAI-compatible). Returns status dict."""
    if not public_llm_base():
        return {
            "rag_enabled": False,
            "message": (
                "Avatar uses Bey default LLM (no Supabase RAG). Set NILA_PUBLIC_BASE_URL "
                "to your public API URL and run POST /api/avatar/setup again."
            ),
        }

    api_id = await ensure_external_llm_api(client)
    patch = {
        "system_prompt": os.getenv("BEY_AGENT_SYSTEM_PROMPT", NILA_ELEVEN_PROMPT),
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
        timeout=60.0,
    )
    if not response.is_success:
        raise http_error(response, "Could not create Beyond Presence call")
    return response.json()


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
