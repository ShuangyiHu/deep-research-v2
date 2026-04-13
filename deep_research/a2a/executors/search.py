"""
a2a/executors/search.py
────────────────────────
A2A AgentExecutor for the Search agent.

Wraps deep_research.mcp.search_client.call_web_search() — executes web
searches via the MCP layer so the backend (currently OpenAI WebSearchTool)
can be swapped without touching this file.

Exposes one skill: "execute"

Input  DataPart: SearchInput  (items: list of {query, reason})
Output DataPart: SearchOutput (documents: list of SearchDocumentData)

Searches are fanned out in parallel inside the executor using asyncio.gather.
The pipeline no longer needs its own fanout — it calls this once with a batch.

Building the A2A application:
    from deep_research.a2a.executors.search import build_search_app
    build_search_app().add_routes_to_app(fastapi_app, ...)
"""
from __future__ import annotations

import asyncio
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    DataPart,
    TaskState,
)

from deep_research.a2a.schemas import SearchDocumentData, SearchInput, SearchOutput

logger = logging.getLogger(__name__)

_AGENT_NAME = "search"
_SKILL_ID = "execute"


# ── AgentCard ─────────────────────────────────────────────────────────────────

def _make_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="Search Agent",
        description=(
            "Executes web search queries via the MCP search layer. "
            "Accepts a batch of {query, reason} items and fans them out in parallel. "
            "Returns a SearchDocumentCollection with one document per query."
        ),
        url=f"{base_url}/a2a/{_AGENT_NAME}",
        version="1.0.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(),
        skills=[
            AgentSkill(
                id=_SKILL_ID,
                name="Execute",
                description=(
                    "Execute a batch of web searches in parallel. "
                    "Each item provides a query string and a reason for the search."
                ),
                tags=["search", "web", "retrieval"],
                examples=["Execute 4 searches for 'AI hiring trends' with different angles"],
            )
        ],
    )


# ── AgentExecutor ─────────────────────────────────────────────────────────────

class SearchExecutor(AgentExecutor):
    """
    Receives SearchInput via DataPart, fans out call_web_search() in parallel,
    returns SearchOutput.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, task_id=context.task_id, context_id=context.context_id)
        await updater.start_work()

        try:
            payload = _extract_input(context)
            inp = SearchInput.model_validate(payload)
            logger.info("[SearchExecutor] execute: %d items", len(inp.items))

            # Fan out all searches in parallel via MCP client
            documents = await _run_searches_parallel(inp)

            out = SearchOutput(documents=documents)
            await updater.add_artifact(
                parts=[DataPart(data=out.model_dump())],
                name="search_result",
                last_chunk=True,
            )
            await updater.complete()
            logger.info("[SearchExecutor] done: %d docs out", len(documents))

        except Exception as exc:
            logger.exception("[SearchExecutor] failed: %s", exc)
            await updater.update_status(
                state=TaskState.failed,
                final=True,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, task_id=context.task_id, context_id=context.context_id)
        await updater.update_status(state=TaskState.canceled, final=True)


# ── Search fanout ─────────────────────────────────────────────────────────────

async def _search_one(query: str) -> SearchDocumentData:
    """Run one web search via the MCP client, return a SearchDocumentData."""
    from deep_research.mcp.search_client import call_web_search
    results = await call_web_search(query)
    content = results[0]["content"] if results else ""
    return SearchDocumentData(content=content, query=query)


async def _run_searches_parallel(inp: SearchInput) -> list[SearchDocumentData]:
    """Fan out all items in parallel, preserving order."""
    tasks = [
        asyncio.create_task(_search_one(item.query))
        for item in inp.items
    ]
    return list(await asyncio.gather(*tasks))


# ── App factory ───────────────────────────────────────────────────────────────

def build_search_app(base_url: str = "http://localhost:8000") -> A2AFastAPIApplication:
    from deep_research.core.config import settings
    resolved_base = settings.a2a_search_url or base_url

    card = _make_agent_card(resolved_base)
    handler = DefaultRequestHandler(
        agent_executor=SearchExecutor(),
        task_store=InMemoryTaskStore(),
    )
    return A2AFastAPIApplication(agent_card=card, http_handler=handler)


# ── Input extraction helper ───────────────────────────────────────────────────

def _extract_input(context: RequestContext) -> dict:
    """Extract the DataPart 'input' dict from the incoming A2A message."""
    for part in context.message.parts:
        p = getattr(part, "root", part)
        if hasattr(p, "data") and isinstance(p.data, dict):
            return p.data.get("input", p.data)
    raise ValueError("No DataPart found in request message")
