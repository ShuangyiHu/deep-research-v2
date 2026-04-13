"""
test_mcp_search.py
──────────────────
Verifies the MCP search client/server wiring.
No real web searches are made — the underlying _web_search_impl is mocked.
"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_call_web_search_in_process_returns_list():
    """In-process mode: call_web_search returns a list with content and query."""
    with patch(
        "deep_research.mcp.search_server._web_search_impl",
        new_callable=AsyncMock,
        return_value="mocked search result text",
    ):
        from deep_research.mcp.search_client import call_web_search
        results = await call_web_search("AI trends 2025")

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["content"] == "mocked search result text"
    assert results[0]["query"] == "AI trends 2025"


@pytest.mark.asyncio
async def test_call_web_search_propagates_query():
    """The query string must be preserved in the result dict."""
    test_query = "quantum computing breakthroughs"
    with patch(
        "deep_research.mcp.search_server._web_search_impl",
        new_callable=AsyncMock,
        return_value="some content",
    ):
        from deep_research.mcp.search_client import call_web_search
        results = await call_web_search(test_query)

    assert results[0]["query"] == test_query


@pytest.mark.asyncio
async def test_http_mode_raises_not_implemented():
    """HTTP mode is not yet implemented — should raise NotImplementedError."""
    from deep_research.mcp.search_client import _call_http
    with pytest.raises(NotImplementedError):
        await _call_http("any query")


@pytest.mark.asyncio
async def test_mcp_server_tool_wraps_impl():
    """web_search MCP tool should call _web_search_impl and return structured list."""
    with patch(
        "deep_research.mcp.search_server._web_search_impl",
        new_callable=AsyncMock,
        return_value="server result",
    ):
        from deep_research.mcp.search_server import web_search
        results = await web_search("test topic")

    assert results[0]["content"] == "server result"
    assert results[0]["query"] == "test topic"
