"""
pipeline.py — top-level orchestration.

Key change: search_results from the draft phase are threaded through the
entire iterative loop so every evaluation call can fact-check against
actual evidence rather than training-data knowledge.
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

logger = logging.getLogger(__name__)


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


async def iterative_loop(
    report: str,
    search_results: str,          # ← passed through from draft phase
    threshold: int | None = None,
    max_iter: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[str, dict, int]:
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

        feedback = await consensus_evaluation(
            report, search_results=search_results, on_progress=on_progress
        )
        current_score: int = feedback["score"]

        if current_score > best_score:
            best_score = current_score
            best_report = report
            best_feedback = feedback
            _emit(on_progress, f"✓ New best score: {best_score}")
        elif current_score < best_score:
            _emit(on_progress,
                  f"↩ Score regressed ({current_score} < {best_score}), reverting")
            report = best_report
            feedback = await consensus_evaluation(
                report, search_results=search_results, on_progress=on_progress
            )
            _emit(on_progress, f"Score after revert: {feedback['score']}")

        if best_score >= threshold:
            _emit(on_progress, f"✓ Quality threshold ({threshold}) reached.")
            break

        evidence: str | None = None
        if (feedback.get("needs_more_search")
                and search_count < max_targeted
                and feedback.get("search_queries")):
            new_evidence = await targeted_search(
                feedback["search_queries"], on_progress=on_progress
            )
            evidence = new_evidence
            # Accumulate new evidence into search_results for future eval rounds
            search_results = search_results + "\n\n---\n\n" + new_evidence
            search_count += 1

        report = await rewrite_sections(report, feedback, evidence, on_progress=on_progress)

    if not best_feedback:
        best_feedback = feedback

    return best_report, best_feedback, iterations_done


async def run_pipeline_async(
    query: str,
    threshold: int | None = None,
    max_iter: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    with trace("DeepResearch Pipeline"):
        _emit(on_progress, "═══ PHASE 1: Drafting ═══")
        # draft_report now returns (report, search_results)
        draft, search_results = await draft_report(query, on_progress=on_progress)

        _emit(on_progress, "\n═══ PHASE 2: Iterative Refinement ═══")
        final_report, final_feedback, iters_done = await iterative_loop(
            draft,
            search_results=search_results,
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