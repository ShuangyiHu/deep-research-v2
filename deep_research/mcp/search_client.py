"""
mcp/search_client.py
─────────────────────
MCP Client used by SearchExecutor to call the web_search tool.

In-process mode (MCP_SEARCH_MODE=in-process, default):
    Calls the search_server functions directly — no network hop, no subprocess.
    Fastest and simplest for same-process deployment.

HTTP mode (MCP_SEARCH_MODE=http, future):
    Connects to a standalone MCP server over streamable-http transport.
    Enable when SearchExecutor is split into its own container.
"""
from __future__ import annotations

import logging
from typing import Any

from deep_research.core.config import settings

logger = logging.getLogger(__name__)


async def call_web_search(query: str) -> list[dict[str, Any]]:
    """
    Call the web_search MCP tool and return a list of result dicts.

    Each dict: {"content": str, "query": str}
    """
    if settings.mcp_search_mode == "in-process":
        return await _call_in_process(query)
    else:
        return await _call_http(query)


async def _call_in_process(query: str) -> list[dict[str, Any]]:
    """Direct in-process call — imports the MCP server function directly."""
    from deep_research.mcp.search_server import _web_search_impl
    content = await _web_search_impl(query)
    logger.info("[MCP/client] in-process web_search returned %d chars", len(content))
    return [{"content": content, "query": query}]


async def _call_http(query: str) -> list[dict[str, Any]]:
    """
    HTTP MCP client — connects to a standalone MCP server.
    Populated when MCP_SEARCH_MODE=http is needed (split-container deploy).
    """
    raise NotImplementedError(
        "HTTP MCP transport not yet configured. "
        "Set MCP_SEARCH_MODE=in-process or implement this path."
    )
