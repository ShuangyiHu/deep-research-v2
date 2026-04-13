"""
deep_research.mcp
─────────────────
Model Context Protocol (MCP) tool abstraction layer.

Why MCP:
    SearchExecutor currently embeds WebSearchTool directly (OpenAI Agents SDK).
    Wrapping web search behind MCP lets us swap the implementation
    (OpenAI → Tavily → Brave → custom) without changing SearchExecutor.

    "A2A handles inter-agent communication, while MCP standardizes
    tool invocation within each agent."

Structure:
  search_server.py  — MCP Server exposing web_search(query) → list[dict]
  search_client.py  — MCP Client used by SearchExecutor

Populated in Phase B. Importable from Phase A.
"""
