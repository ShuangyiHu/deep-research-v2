"""
a2a/executors/writer.py
────────────────────────
A2A AgentExecutor for the Writer agent.

Wraps deep_research.core.writer.draft_report() — the longest-running LLM call
in the pipeline (1000+ word report generation). Isolated here so it can be
independently scaled or swapped for a more powerful model in future.

Exposes one skill: "draft"

Input  DataPart: WriteInput  (query: str, documents: list[SearchDocumentData])
Output DataPart: WriteOutput (report: str — complete markdown)

Building the A2A application:
    from deep_research.a2a.executors.writer import build_writer_app
    build_writer_app().add_routes_to_app(fastapi_app, ...)
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

from deep_research.a2a.schemas import WriteInput, WriteOutput
from deep_research.core.search_documents import SearchDocument, SearchDocumentCollection

logger = logging.getLogger(__name__)

_AGENT_NAME = "writer"
_SKILL_ID = "draft"


# ── AgentCard ─────────────────────────────────────────────────────────────────

def _make_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="Writer Agent",
        description=(
            "Generates a structured research report in markdown from a query and "
            "a scored, deduplicated SearchDocumentCollection. "
            "Uses WriterAgent (gpt-4o-mini) for the generation step."
        ),
        url=f"{base_url}/a2a/{_AGENT_NAME}",
        version="1.0.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(),
        skills=[
            AgentSkill(
                id=_SKILL_ID,
                name="Draft",
                description=(
                    "Produce a 1000+ word markdown research report from a query "
                    "and pre-scored search evidence."
                ),
                tags=["writing", "report", "synthesis"],
                examples=["Draft a report on 'AI impact on employment' from 8 search docs"],
            )
        ],
    )


# ── AgentExecutor ─────────────────────────────────────────────────────────────

class WriterExecutor(AgentExecutor):
    """
    Receives WriteInput via DataPart, calls draft_report() with the pre-built
    collection, and returns WriteOutput containing the markdown report.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, task_id=context.task_id, context_id=context.context_id)
        await updater.start_work()

        try:
            payload = _extract_input(context)
            inp = WriteInput.model_validate(payload)
            logger.info("[WriterExecutor] draft: query=%r, %d docs",
                        inp.query[:60], len(inp.documents))

            # Reconstruct SearchDocumentCollection from wire format
            collection = SearchDocumentCollection(
                documents=[
                    SearchDocument.from_dict(d.model_dump()) for d in inp.documents
                ]
            )

            # draft_report with pre-built collection skips the internal search phase
            from deep_research.core.writer import draft_report
            report, _ = await draft_report(inp.query, collection=collection)

            out = WriteOutput(report=report)
            await updater.add_artifact(
                parts=[DataPart(data=out.model_dump())],
                name="draft_result",
                last_chunk=True,
            )
            await updater.complete()
            logger.info("[WriterExecutor] done: %d chars", len(report))

        except Exception as exc:
            logger.exception("[WriterExecutor] failed: %s", exc)
            await updater.update_status(
                state=TaskState.failed,
                final=True,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, task_id=context.task_id, context_id=context.context_id)
        await updater.update_status(state=TaskState.canceled, final=True)


# ── App factory ───────────────────────────────────────────────────────────────

def build_writer_app(base_url: str = "http://localhost:8000") -> A2AFastAPIApplication:
    from deep_research.core.config import settings
    resolved_base = settings.a2a_writer_url or base_url

    card = _make_agent_card(resolved_base)
    handler = DefaultRequestHandler(
        agent_executor=WriterExecutor(),
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
