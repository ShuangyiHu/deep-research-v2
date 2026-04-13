"""
a2a/executors/evaluator.py
───────────────────────────
A2A AgentExecutor for the Evaluator agent.

Wraps deep_research.core.evaluator.consensus_evaluation() — dual-model
Claude+Gemini evaluation run in parallel. The most architecturally interesting
executor because it implements **agent-initiated search**:

    When needs_more_search=True AND search_budget_remaining > 0,
    this executor autonomously calls the Search A2A client and then the
    Analyst A2A client to gather supplemental evidence — without waiting for
    pipeline.py to decide. This is the "collaborative" step toward a more
    autonomous multi-agent network.

Agent-initiated flow (one hop: evaluate → search + analyse):
    1. Run consensus_evaluation() (Claude ∥ Gemini)
    2. If evidence gap detected and budget allows:
         a. call search_execute() for the suggested queries
         b. call analyst_analyse() to score/dedup the new docs
         c. attach the analysed docs as collected_evidence in the response
    3. Return EvaluateOutput with collected_evidence + budget_consumed

The pipeline then merges collected_evidence into its running collection
before the rewrite step — it no longer needs to inspect needs_more_search
to decide whether to search.

Failure isolation: if the internal Search/Analyst calls fail, the exception
is caught, logged, and the executor returns as if no supplemental search was
done. The pipeline receives budget_consumed=0 and continues normally.

Exposes one skill: "evaluate"

Building the A2A application:
    from deep_research.a2a.executors.evaluator import build_evaluator_app
    build_evaluator_app().add_routes_to_app(fastapi_app, ...)
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

from deep_research.a2a.schemas import (
    EvaluateInput,
    EvaluateOutput,
    SearchDocumentData,
    SearchItemData,
)

logger = logging.getLogger(__name__)

_AGENT_NAME = "evaluator"
_SKILL_ID = "evaluate"


# ── AgentCard ─────────────────────────────────────────────────────────────────

def _make_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="Evaluator Agent",
        description=(
            "Dual-model consensus evaluation (Claude + Gemini) of a research report "
            "against web search evidence. Implements agent-initiated search: when the "
            "evaluator detects an evidence gap and has remaining search budget, it "
            "autonomously triggers the Search and Analyst agents before returning."
        ),
        url=f"{base_url}/a2a/{_AGENT_NAME}",
        version="1.0.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(),
        skills=[
            AgentSkill(
                id=_SKILL_ID,
                name="Evaluate",
                description=(
                    "Score a report 1-10, identify weak sections, and optionally "
                    "trigger supplemental searches autonomously (agent-initiated flow)."
                ),
                tags=["evaluation", "quality", "consensus", "agent-initiated"],
                examples=["Evaluate a report on 'AI employment impact' with budget=2"],
            )
        ],
    )


# ── AgentExecutor ─────────────────────────────────────────────────────────────

class EvaluatorExecutor(AgentExecutor):
    """
    1. Runs consensus_evaluation() (Claude ∥ Gemini).
    2. If needs_more_search AND budget > 0:
         - Calls search_execute() for suggested queries (via A2A).
         - Calls analyst_analyse() on the new docs (via A2A).
         - Attaches scored docs as collected_evidence in the response.
    3. Returns EvaluateOutput.

    Pipeline receives collected_evidence and merges it; budget_consumed tells
    pipeline how much of its search_budget was spent this round.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, task_id=context.task_id, context_id=context.context_id)
        await updater.start_work()

        try:
            payload = _extract_input(context)
            inp = EvaluateInput.model_validate(payload)
            logger.info(
                "[EvaluatorExecutor] evaluate: report=%d chars, budget=%d",
                len(inp.report), inp.search_budget_remaining,
            )

            # Step 1: Consensus evaluation (Claude + Gemini in parallel)
            from deep_research.core.evaluator import consensus_evaluation
            feedback = await consensus_evaluation(
                inp.report,
                search_results=inp.search_results,
            )

            # Step 2: Agent-initiated supplemental search (if budget allows)
            collected_evidence: list[SearchDocumentData] | None = None
            budget_consumed = 0

            if (
                feedback.get("needs_more_search")
                and inp.search_budget_remaining > 0
                and feedback.get("search_queries")
            ):
                collected_evidence, budget_consumed = await _do_supplemental_search(
                    queries=feedback["search_queries"],
                    query=inp.query,
                )
                if collected_evidence:
                    logger.info(
                        "[EvaluatorExecutor] agent-initiated search: %d new docs, budget_consumed=%d",
                        len(collected_evidence), budget_consumed,
                    )

            out = EvaluateOutput(
                score=feedback["score"],
                weak_sections=feedback.get("weak_sections", []),
                needs_more_search=feedback.get("needs_more_search", False),
                search_queries=feedback.get("search_queries", []),
                rewrite_instructions=feedback.get("rewrite_instructions", {}),
                claude_score=feedback.get("claude_score", 0),
                gemini_score=feedback.get("gemini_score", 0),
                claude_reasoning=feedback.get("claude_reasoning", ""),
                gemini_reasoning=feedback.get("gemini_reasoning", ""),
                disagreement_note=feedback.get("disagreement_note", ""),
                collected_evidence=collected_evidence,
                budget_consumed=budget_consumed,
            )

            await updater.add_artifact(
                parts=[DataPart(data=out.model_dump())],
                name="evaluate_result",
                last_chunk=True,
            )
            await updater.complete()
            logger.info(
                "[EvaluatorExecutor] done: score=%d, evidence=%s",
                out.score,
                f"{len(collected_evidence)} docs" if collected_evidence else "none",
            )

        except Exception as exc:
            logger.exception("[EvaluatorExecutor] failed: %s", exc)
            await updater.update_status(
                state=TaskState.failed,
                final=True,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, task_id=context.task_id, context_id=context.context_id)
        await updater.update_status(state=TaskState.canceled, final=True)


# ── Agent-initiated supplemental search ──────────────────────────────────────

async def _do_supplemental_search(
    queries: list[str],
    query: str,
) -> tuple[list[SearchDocumentData] | None, int]:
    """
    Autonomously fetch supplemental evidence when the evaluator detects an
    evidence gap. Calls Search A2A → Analyst A2A in sequence.

    Returns:
        (scored_docs, budget_consumed) — budget_consumed is 1 on success, 0 on failure.

    Failure isolation: any exception is caught and logged; caller receives
    (None, 0) so the pipeline continues without supplemental evidence.
    """
    try:
        from deep_research.a2a.clients import analyst_analyse, search_execute
        from deep_research.core.config import settings
        from deep_research.core.search_documents import SearchDocumentCollection

        # Cap queries to configured limit
        cap = settings.pipeline_search_query_cap
        capped = queries[:cap]

        # Nothing to search
        if not capped:
            return None, 0
        logger.info(
            "[EvaluatorExecutor] agent-initiated: calling search_execute for %d queries",
            len(capped),
        )

        raw_collection = await search_execute([
            SearchItemData(query=q, reason="Evaluator-initiated evidence retrieval")
            for q in capped
        ])

        logger.info(
            "[EvaluatorExecutor] agent-initiated: calling analyst_analyse on %d docs",
            len(raw_collection.documents),
        )
        scored_collection = await analyst_analyse(raw_collection, query)

        docs = [
            SearchDocumentData(**doc.to_dict())
            for doc in scored_collection.documents
        ]
        return docs, 1

    except Exception as exc:
        logger.warning(
            "[EvaluatorExecutor] agent-initiated search failed (degrading gracefully): %s",
            exc,
        )
        return None, 0


# ── App factory ───────────────────────────────────────────────────────────────

def build_evaluator_app(base_url: str = "http://localhost:8000") -> A2AFastAPIApplication:
    from deep_research.core.config import settings
    resolved_base = settings.a2a_evaluator_url or base_url

    card = _make_agent_card(resolved_base)
    handler = DefaultRequestHandler(
        agent_executor=EvaluatorExecutor(),
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
