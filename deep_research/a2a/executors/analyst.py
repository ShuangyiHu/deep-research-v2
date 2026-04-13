"""
a2a/executors/analyst.py
─────────────────────────
A2A AgentExecutor for the Analyst agent.

Wraps deep_research.core.analysis.analyse() — no LLM, pure Python.
Exposes one skill: "analyse"

Input  DataPart: AnalyseInput schema  (documents list + query string)
Output DataPart: AnalyseOutput schema (scored, deduplicated documents)

Building the A2A application:
    from deep_research.a2a.executors.analyst import build_analyst_app
    build_analyst_app().add_routes_to_app(fastapi_app, prefix="/a2a/analyst")
"""
from __future__ import annotations

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

from deep_research.a2a.schemas import AnalyseInput, AnalyseOutput, SearchDocumentData
from deep_research.core.analysis import analyse
from deep_research.core.search_documents import SearchDocument, SearchDocumentCollection

logger = logging.getLogger(__name__)

_AGENT_NAME = "analyst"
_SKILL_ID = "analyse"


# ── AgentCard ─────────────────────────────────────────────────────────────────

def _make_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="Analyst Agent",
        description=(
            "Deduplicates and relevance-scores a SearchDocumentCollection "
            "using TF-IDF keyword matching. No LLM — pure Python. "
            "Reusable: any system with search documents can call this."
        ),
        url=f"{base_url}/a2a/{_AGENT_NAME}",
        version="1.0.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(),
        skills=[
            AgentSkill(
                id=_SKILL_ID,
                name="Analyse",
                description=(
                    "Deduplicate (Jaccard similarity) and relevance-score "
                    "(TF-IDF) a list of SearchDocuments against a query."
                ),
                tags=["search", "deduplication", "relevance-scoring"],
                examples=["Analyse 10 raw search docs for query 'AI hiring trends'"],
            )
        ],
    )


# ── AgentExecutor ─────────────────────────────────────────────────────────────

class AnalystExecutor(AgentExecutor):
    """
    Receives AnalyseInput via DataPart, runs analyse(), returns AnalyseOutput.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, task_id=context.task_id, context_id=context.context_id)
        await updater.start_work()

        try:
            payload = _extract_input(context)
            inp = AnalyseInput.model_validate(payload)
            logger.info("[AnalystExecutor] analyse: %d docs, query=%r",
                        len(inp.documents), inp.query[:60])

            # Convert DataPart docs → SearchDocumentCollection
            raw_collection = SearchDocumentCollection(
                documents=[
                    SearchDocument.from_dict(d.model_dump()) for d in inp.documents
                ]
            )

            # Run pure-Python Analyst (no LLM, fast)
            scored = analyse(raw_collection, query=inp.query)

            # Build output
            out = AnalyseOutput(
                documents=[
                    SearchDocumentData(**doc.to_dict()) for doc in scored.documents
                ]
            )

            await updater.add_artifact(
                parts=[DataPart(data=out.model_dump())],
                name="analyse_result",
                last_chunk=True,
            )
            await updater.complete()
            logger.info("[AnalystExecutor] done: %d docs out", len(out.documents))

        except Exception as exc:
            logger.exception("[AnalystExecutor] failed: %s", exc)
            await updater.update_status(
                state=TaskState.failed,
                final=True,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, task_id=context.task_id, context_id=context.context_id)
        await updater.update_status(state=TaskState.canceled, final=True)


# ── App factory ───────────────────────────────────────────────────────────────

def build_analyst_app(base_url: str = "http://localhost:8000") -> A2AFastAPIApplication:
    from deep_research.core.config import settings
    resolved_base = settings.a2a_analyst_url or base_url

    card = _make_agent_card(resolved_base)
    handler = DefaultRequestHandler(
        agent_executor=AnalystExecutor(),
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
