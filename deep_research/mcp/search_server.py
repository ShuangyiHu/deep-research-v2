"""
mcp/search_server.py
─────────────────────
MCP Server: exposes web_search(query) as a standardised tool.

Why MCP here:
    SearchExecutor currently embeds WebSearchTool (OpenAI Agents SDK) directly.
    Wrapping behind MCP means swapping to Tavily / Brave / custom retrieval
    later requires only changing this file — SearchExecutor stays unchanged.

    "A2A handles inter-agent communication, while MCP standardizes
    tool invocation within each agent."

Current implementation: delegates to OpenAI Agents SDK WebSearchTool via a
lightweight Runner.run() call — same behaviour as before, just now behind a
stable tool interface.

Future swap: replace _web_search_impl() body only; interface stays constant.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# MCP server instance (in-process mode: imported and called directly)
mcp = FastMCP("deep-research-search")


@mcp.tool()
async def web_search(query: str) -> list[dict[str, Any]]:
    """
    Search the web for the given query and return a list of result summaries.

    Each result dict has:
      - content: str   — 2-3 paragraph summary (~300 words)
      - query:   str   — the search query that produced this result
    """
    logger.info("[MCP/web_search] query=%r", query)
    content = await _web_search_impl(query)
    return [{"content": content, "query": query}]


async def _web_search_impl(query: str) -> str:
    """
    Execute a single web search using the OpenAI Agents SDK WebSearchTool.

    This is the only place that knows about the underlying search backend.
    Replace this function body to swap providers (Tavily, Brave, etc.).
    """
    from agents import Agent, WebSearchTool, Runner
    from agents.model_settings import ModelSettings

    agent = Agent(
        name="MCPSearchAgent",
        instructions=(
            "Search the web for the given term and return a faithful digest of "
            "what the search results actually say. This digest is the ONLY "
            "evidence a downstream writer and evaluator will see — anything you "
            "omit will be invisible to them, and anything you invent will be "
            "flagged as unverifiable.\n\n"
            "Rules:\n"
            "- Preserve specific facts, numbers, dates, names, quotes verbatim.\n"
            "- For EACH distinct source you draw from, include a line:\n"
            "    SOURCE: <full URL> — <publisher or title>\n"
            "  Use the exact URLs returned by the web search tool. Never invent,\n"
            "  shorten, or reconstruct a URL. If a result has no URL, write\n"
            "  SOURCE: (no url) — <publisher>.\n"
            "- After the SOURCE lines, write a summary of 400–700 words that\n"
            "  cites each claim inline as (SOURCE: <url>). Do NOT add your own\n"
            "  knowledge; stick to what the search results say.\n"
            "- If the results conflict, report both views."
        ),
        tools=[WebSearchTool(search_context_size="low")],
        model="gpt-4o-mini",
        model_settings=ModelSettings(tool_choice="required"),
    )
    result = await Runner.run(agent, f"Search term: {query}\nReason: research evidence")
    return str(result.final_output)
