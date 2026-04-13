"""
pipeline.py
───────────
Top-level orchestration: wires all agents together into the full pipeline.

Agent execution order (final A2A + MCP architecture):

  [Phase 1 — Drafting]
    QueryRewriterAgent  → rewrite_query()     (query_rewriter.py)  [local]
    PlannerAgent        → plan_searches()      (planner.py)         [local]
    SearchAgent ×N      → search_execute()     (a2a/clients.py)     [A2A]
      └─ internally uses MCP search_client → web_search MCP server
    AnalystAgent        → analyst_analyse()    (a2a/clients.py)     [A2A]
    WriterAgent         → writer_draft()       (a2a/clients.py)     [A2A]

  [Phase 2 — Iterative Refinement loop, up to max_iter times]
    EvaluatorAgent      → evaluator_evaluate() (a2a/clients.py)     [A2A]
      └─ agent-initiated: if needs_more_search + budget > 0,
         EvaluatorExecutor autonomously calls Search A2A + Analyst A2A
         and returns collected_evidence in the response.
    RewriteAgent + StructureAgent → rewrite_sections() (rewriter.py) [local]

Design principles:
  - Planner / QueryRewriter / Rewriter / Structure stay local (cheap, tightly coupled).
  - Search / Analyst / Writer / Evaluator are A2A services (expensive, reusable, scalable).
  - MCP abstracts the web search tool inside SearchExecutor (swap provider = 1 file change).
  - pipeline.py owns global state only: budget tracking, regression rollback, progress events.
  - EvaluatorExecutor owns supplemental search decisions (agent-initiated flow).
"""

import asyncio
import logging
from typing import Callable

from agents import trace

from deep_research.core.config import settings
from deep_research.core.rewriter import rewrite_sections
from deep_research.core.planner import plan_searches
from deep_research.core.search_documents import SearchDocumentCollection
from deep_research.core.query_rewriter import rewrite_query
# Phase C: Analyst now called via A2A client
# Phase D: Search now called via A2A client
# Phase E: Writer now called via A2A client
# Phase F: Evaluator now called via A2A client (agent-initiated search inside)
from deep_research.a2a.clients import (
    analyst_analyse,
    evaluator_evaluate,
    search_execute,
    writer_draft,
)
from deep_research.a2a.schemas import SearchItemData

logger = logging.getLogger(__name__)


# ── Quality section builder (unchanged) ───────────────────────────────────────

def _build_quality_section(feedback: dict, total_iterations: int) -> str:
    score = feedback.get("score", "N/A")
    c_score = feedback.get("claude_score", "?")
    g_score = feedback.get("gemini_score", "?")
    c_reasoning = feedback.get("claude_reasoning", "")
    g_reasoning = feedback.get("gemini_reasoning", "")
    disagreement = feedback.get("disagreement_note", "")
    weak = feedback.get("weak_sections", [])

    lines = [
        "\n\n---\n",
        "## 📊 Report Quality Assessment",
        "",
        f"**Final consensus score: {score} / 10** "
        f"(Claude: {c_score} · Gemini: {g_score} · "
        f"Iterations: {total_iterations})",
        "",
    ]
    if c_reasoning:
        lines += [f"**Claude:** {c_reasoning}", ""]
    if g_reasoning:
        lines += [f"**Gemini:** {g_reasoning}", ""]
    if disagreement:
        lines += [
            f"> ⚠️ **Evaluator disagreement** (gap ≥ {settings.pipeline_score_gap_threshold} pts). "
            "The two AI reviewers rated this report significantly differently. "
            "The lower (more conservative) score was used as the consensus. "
            "Cross-check key claims independently.",
            "",
        ]
    if weak:
        lines += [f"**Sections flagged for improvement:** {', '.join(weak)}", ""]
    lines.append(
        "*Evaluated by Claude (claude-sonnet-4-6) and Gemini (gemini-2.0-flash) "
        "using the actual web search results as the factual ground truth.*"
    )
    return "\n".join(lines)


# ── Iterative refinement loop ─────────────────────────────────────────────────

async def iterative_loop(
    report: str,
    collection: SearchDocumentCollection,      # ← was str in v1
    query: str,                                 # ← NEW: needed for Analyst re-scoring
    threshold: int | None = None,
    max_iter: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[str, dict, int]:
    """
    Iteratively evaluate and rewrite the report until quality threshold is met.

    Input:
        report:     Initial draft markdown string.
        collection: SearchDocumentCollection from the draft phase (post-Analyst).
        query:      Rewritten query — forwarded to evaluator_evaluate so the
                    EvaluatorExecutor can pass it to Analyst if it triggers
                    an agent-initiated supplemental search.

    Output:
        (best_report, best_feedback, iterations_done)

    Regression rollback:
        If a rewrite causes the consensus score to drop below the best seen,
        the previous best report is restored and re-evaluated (budget=0, no new
        searches) before continuing. This prevents delivering a degraded report.

    Agent-initiated search (Phase F):
        evaluator_evaluate() passes search_budget_remaining to EvaluatorExecutor.
        If the executor autonomously searches, it returns collected_evidence in
        the feedback dict. Pipeline merges those docs into the main collection.

    Rewrite grounding:
        rewrite_sections receives BOTH the full collection (as full_evidence) and
        the newly collected supplemental docs (as new_evidence). This prevents the
        rewriter from filling gaps with training knowledge when revising sections
        that were not directly addressed by agent-initiated search.
    """
    threshold = threshold if threshold is not None else settings.pipeline_quality_threshold
    max_iter = max_iter if max_iter is not None else settings.pipeline_max_iterations
    max_targeted = settings.pipeline_max_targeted_searches

    best_report = report
    best_score = 0
    best_feedback: dict = {}
    search_count = 0
    iterations_done = 0
    feedback: dict = {}

    for i in range(max_iter):
        _emit(on_progress, f"── Iteration {i + 1}/{max_iter} ──────────────────")
        iterations_done = i + 1

        # Phase F: Evaluate via A2A — EvaluatorExecutor may autonomously trigger
        # supplemental search when needs_more_search=True and budget allows.
        remaining_budget = max_targeted - search_count
        feedback = await evaluator_evaluate(
            report, collection, query,
            search_budget_remaining=remaining_budget,
        )
        current_score: int = feedback["score"]

        if current_score > best_score:
            best_score = current_score
            best_report = report
            best_feedback = feedback
            _emit(on_progress, f"✓ New best score: {best_score}")
        elif current_score < best_score:
            # Regression detected — revert to best known version
            _emit(on_progress,
                  f"↩ Score regressed ({current_score} < {best_score}), reverting")
            report = best_report
            # Re-evaluate the reverted report; budget=0 to avoid spending search budget
            # during a rollback re-check
            feedback = await evaluator_evaluate(
                report, collection, query,
                search_budget_remaining=0,
            )
            _emit(on_progress, f"Score after revert: {feedback['score']}")

        if best_score >= threshold:
            _emit(on_progress, f"✓ Quality threshold ({threshold}) reached.")
            break

        # Merge any supplemental evidence the EvaluatorExecutor collected autonomously
        evidence_collection: SearchDocumentCollection | None = None
        collected = feedback.get("collected_evidence")
        budget_used: int = feedback.get("budget_consumed", 0)
        if collected:
            from deep_research.core.search_documents import SearchDocument
            new_docs = [SearchDocument.from_dict(d) for d in collected]
            collection.documents.extend(new_docs)
            evidence_collection = SearchDocumentCollection(documents=new_docs)
            search_count += budget_used
            _emit(on_progress,
                  f"  Merged {len(new_docs)} supplemental docs (budget used: {budget_used})")

        # Rewrite weak sections grounded in the FULL collection (original + all
        # supplementals merged so far). Passing only new_evidence would force the
        # rewriter to rely on training knowledge for unchanged sections — that's
        # exactly how unverifiable claims leak in.
        full_evidence_str: str = collection.to_eval_string()
        new_evidence_str: str | None = (
            evidence_collection.to_eval_string() if evidence_collection else None
        )
        report = await rewrite_sections(
            report, feedback,
            full_evidence=full_evidence_str,
            new_evidence=new_evidence_str,
            on_progress=on_progress,
        )

    if not best_feedback:
        best_feedback = feedback

    return best_report, best_feedback, iterations_done


# ── Top-level pipeline ─────────────────────────────────────────────────────────

async def run_pipeline_async(
    query: str,
    threshold: int | None = None,
    max_iter: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """
    Full pipeline: query → agents → polished report with quality section.

    Phase 1 — Drafting:
        QueryRewriterAgent (local) → PlannerAgent (local) →
        SearchAgent ×N (A2A) → AnalystAgent (A2A) → WriterAgent (A2A)

    Phase 2 — Iterative refinement (up to max_iter):
        EvaluatorAgent (A2A, agent-initiated) → RewriteAgent (local)

    Search budget scaling:
        When QueryRewriterAgent expands a vague query, the rewritten query
        covers more dimensions than the original. A fixed search count designed
        for a 3-word keyword is underpowered for a 50-word multi-dimensional
        question. So: expanded queries receive PIPELINE_HOW_MANY_SEARCHES + 2
        searches to match the increased dimensional scope.
    """
    with trace("DeepResearch Pipeline"):
        _emit(on_progress, "═══ PHASE 1: Drafting ═══")

        # Stage 1: QueryRewriterAgent — expand vague queries before any search
        rewritten_query, was_expanded = await rewrite_query(query, on_progress=on_progress)

        # Proportional search budget: expanded queries get 2 extra searches
        n_searches = (
            settings.pipeline_how_many_searches + 2
            if was_expanded
            else settings.pipeline_how_many_searches
        )
        if was_expanded:
            _emit(on_progress, f"  Search budget: {n_searches} (expanded query +2)")

        # Stage 2: PlannerAgent — generate typed search items (local function)
        search_plan = await plan_searches(
            rewritten_query, on_progress=on_progress, n_searches=n_searches
        )

        # Stage 3: SearchAgent ×N — execute searches via A2A (Phase D)
        _emit(on_progress, "\n─── Search Agent (initial evidence) ───")
        collection = await search_execute([
            SearchItemData(query=item.query, reason=item.reason)
            for item in search_plan.searches
        ])

        # Stage 4: AnalystAgent — deduplicate and score before writing (Phase C/E)
        _emit(on_progress, "\n─── Analyst Agent (initial evidence) ───")
        collection = await analyst_analyse(collection, rewritten_query)

        # Stage 5: WriterAgent — draft report from scored collection (Phase E)
        _emit(on_progress, "\n─── Writer Agent ───")
        draft = await writer_draft(rewritten_query, collection)

        _emit(on_progress, "\n═══ PHASE 2: Iterative Refinement ═══")
        final_report, final_feedback, iters_done = await iterative_loop(
            draft,
            collection=collection,
            query=rewritten_query,       # use rewritten query for Analyst re-scoring
            threshold=threshold,
            max_iter=max_iter,
            on_progress=on_progress,
        )

    quality_section = _build_quality_section(final_feedback, iters_done)
    _emit(on_progress, "✓ Pipeline complete.")
    return final_report + quality_section


def run_pipeline(
    query: str,
    threshold: int | None = None,
    max_iter: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """Synchronous wrapper around run_pipeline_async. Called by Celery worker."""
    return asyncio.run(
        run_pipeline_async(
            query=query,
            threshold=threshold,
            max_iter=max_iter,
            on_progress=on_progress,
        )
    )


def _emit(cb, msg):
    logger.info(msg)
    if cb:
        cb(msg)