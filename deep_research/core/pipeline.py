"""
pipeline.py
───────────
Top-level orchestration: wires all agents together into the full pipeline.

Agent execution order:
  [Phase 1 — Drafting]
    QueryRewriterAgent  → rewrite_query()        (query_rewriter.py) ← NEW Step 3
    PlannerAgent        → plan_searches()         (planner.py)
    SearchAgent ×N      → perform_searches()      (planner.py)
    AnalystAgent        → analyse()               (analysis.py)
    WriterAgent         → draft_report()          (writer.py)

  [Phase 2 — Iterative Refinement loop, up to max_iter times]
    EvaluatorAgent      → consensus_evaluation() (evaluator.py)
    [optional] SearchAgent ×N → targeted_search() (planner.py)
    [optional] AnalystAgent   → analyse()          (analysis.py)     ← NEW Step 2
    RewriteAgent + StructureAgent → rewrite_sections() (rewriter.py)

Key changes from v1:
  - search_results is now SearchDocumentCollection (not str) throughout.
  - AnalystAgent (analyse()) is called after every search phase:
      * after the initial perform_searches()
      * after each targeted_search() in the refinement loop
  - consensus_evaluation() receives .to_eval_string() — its signature unchanged.
  - iterative_loop merges new targeted evidence into the existing collection
    via .documents.extend() instead of string concatenation.
"""

import asyncio
import logging
from typing import Callable

from agents import trace

from deep_research.core.config import settings
from deep_research.core.writer import draft_report
from deep_research.core.evaluator import consensus_evaluation
from deep_research.core.rewriter import rewrite_sections
from deep_research.core.planner import targeted_search
from deep_research.core.analysis import analyse
from deep_research.core.search_documents import SearchDocumentCollection
from deep_research.core.query_rewriter import rewrite_query

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
        query:      Original rewritten query — used to re-score any new evidence
                    added by targeted_search() through the Analyst Agent.

    Output:
        (best_report, best_feedback, iterations_done)

    Regression rollback:
        If a rewrite causes the consensus score to drop below the best seen,
        the previous best report is restored and re-evaluated before continuing.
        This prevents the pipeline from delivering a degraded final report.
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

        # Evaluate against current evidence collection
        feedback = await consensus_evaluation(
            report,
            search_results=collection.to_eval_string(),   # ← stringify here
            on_progress=on_progress,
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
            feedback = await consensus_evaluation(
                report,
                search_results=collection.to_eval_string(),
                on_progress=on_progress,
            )
            _emit(on_progress, f"Score after revert: {feedback['score']}")

        if best_score >= threshold:
            _emit(on_progress, f"✓ Quality threshold ({threshold}) reached.")
            break

        # Optionally fetch more evidence and run Analyst Agent on it
        evidence_collection: SearchDocumentCollection | None = None
        if (feedback.get("needs_more_search")
                and search_count < max_targeted
                and feedback.get("search_queries")):

            raw_new = await targeted_search(
                feedback["search_queries"], on_progress=on_progress
            )

            # Run Analyst Agent on new evidence before merging
            analysed_new = analyse(raw_new, query=query, on_progress=on_progress)

            # Merge into the running collection so future eval rounds see all evidence
            collection.documents.extend(analysed_new.documents)
            evidence_collection = analysed_new
            search_count += 1

        # Rewrite weak sections using new evidence (if any)
        evidence_str: str | None = (
            evidence_collection.to_eval_string() if evidence_collection else None
        )
        report = await rewrite_sections(
            report, feedback, evidence_str, on_progress=on_progress
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
        QueryRewriterAgent → PlannerAgent → SearchAgent ×N → AnalystAgent → WriterAgent

    Phase 2 — Iterative refinement (up to max_iter):
        EvaluatorAgent (Claude+Gemini) → [SearchAgent+AnalystAgent] → RewriteAgent

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

        # Stage 2–4: Plan → Search → Draft
        draft, collection = await draft_report(
            rewritten_query, on_progress=on_progress, n_searches=n_searches
        )

        # Stage 5: AnalystAgent — deduplicate and score initial evidence
        _emit(on_progress, "\n─── Analyst Agent (initial evidence) ───")
        collection = analyse(collection, query=rewritten_query, on_progress=on_progress)

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