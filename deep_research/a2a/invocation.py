"""
a2a/invocation.py
─────────────────
Unified A2A invocation contract.

Single public function:
    await call_agent(name, skill, payload) -> dict

Why this exists:
    Pipeline logic is decoupled from specific agent implementations.
    Swapping an agent (e.g. replacing SearchExecutor with an external Tavily
    A2A service) requires changing only A2A_SEARCH_URL — no pipeline edits.

    "I abstracted agent invocation into a unified contract, decoupling
    pipeline logic from specific agent implementations."

URL resolution (per-agent override beats base URL):
    A2A_SEARCH_URL    overrides  A2A_BASE_URL/search
    A2A_ANALYST_URL   overrides  A2A_BASE_URL/analyst
    A2A_WRITER_URL    overrides  A2A_BASE_URL/writer
    A2A_EVALUATOR_URL overrides  A2A_BASE_URL/evaluator
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from a2a.client import A2AClient
from a2a.types import (
    DataPart,
    Message,
    MessageSendParams,
    Role,
    SendMessageRequest,
)

from deep_research.core.config import settings

logger = logging.getLogger(__name__)

# ── Known agent names for URL resolution ──────────────────────────────────────
_AGENT_NAMES = ("search", "analyst", "writer", "evaluator")


def get_agent_url(name: str) -> str:
    """Resolve the base URL for a named A2A agent.

    Always returns a URL with a trailing slash so Starlette's Mount
    regex (``^/a2a/<name>/(?P<path>.*)$``) matches the RPC POST endpoint.
    """
    if name not in _AGENT_NAMES:
        raise ValueError(f"Unknown A2A agent: '{name}'. Must be one of {_AGENT_NAMES}.")
    override: str | None = getattr(settings, f"a2a_{name}_url", None)
    base = override or f"{settings.a2a_base_url}/{name}"
    return base.rstrip("/") + "/"


async def call_agent(name: str, skill: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Send a DataPart message to an A2A agent and return its DataPart response.

    Args:
        name:    Agent name — one of 'search', 'analyst', 'writer', 'evaluator'.
        skill:   Skill identifier declared in the agent's AgentCard.
        payload: Input dict matching the agent's InputModel schema.

    Returns:
        Output dict matching the agent's OutputModel schema.
    """
    url = get_agent_url(name)
    logger.info("[A2A] → %s/%s  url=%s", name, skill, url)

    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.user,
        parts=[DataPart(data={"skill": skill, "input": payload})],
    )
    request = SendMessageRequest(
        id=str(uuid.uuid4()),
        jsonrpc="2.0",
        method="message/send",
        params=MessageSendParams(message=message),
    )

    async with httpx.AsyncClient(timeout=300.0) as http:
        client = A2AClient(httpx_client=http, url=url)
        response = await client.send_message(request=request)

    result = _extract_data(response, name, skill)
    logger.info("[A2A] ← %s/%s  keys=%s", name, skill, list(result.keys()))
    return result


# ── Response parsing ──────────────────────────────────────────────────────────

def _extract_data(response: Any, name: str, skill: str) -> dict[str, Any]:
    """
    Parse a SendMessageResponse and return the first DataPart payload dict.

    The A2A server (DefaultRequestHandler) returns a completed Task whose
    artifacts list contains the executor's DataPart output.
    """
    # Pydantic v2 RootModel wraps the union; unwrap if present
    obj = getattr(response, "root", response)

    if hasattr(obj, "error"):
        raise RuntimeError(f"A2A agent {name}/{skill} returned error: {obj.error}")

    result = obj.result  # Task | Message

    # Normal path: completed Task with artifact DataPart
    if hasattr(result, "artifacts") and result.artifacts:
        for artifact in result.artifacts:
            for part in artifact.parts:
                data = _unwrap_data_part(part)
                if data is not None:
                    return data

    # Fallback: direct Message response with DataPart
    if hasattr(result, "parts"):
        for part in result.parts:
            data = _unwrap_data_part(part)
            if data is not None:
                return data

    raise RuntimeError(
        f"A2A agent {name}/{skill} returned no DataPart payload. "
        f"Result type: {type(result).__name__}"
    )


def _unwrap_data_part(part: Any) -> dict[str, Any] | None:
    """Return the dict payload if part is (or wraps) a DataPart, else None."""
    # Pydantic discriminated union may wrap in .root
    p = getattr(part, "root", part)
    if hasattr(p, "data") and isinstance(p.data, dict):
        return p.data
    return None
