"""
evaluator.py
────────────
Dual-model evaluation. Both models receive the report AND the search results
it was written from, so Accuracy scoring is grounded in actual evidence
rather than training-data knowledge.
"""

import asyncio
import logging
from typing import Callable

from deep_research.core.clients import claude_client, gemini_client
from deep_research.core.config import (
    CANONICAL_SECTIONS,
    EVAL_PROMPT_TEMPLATE,
    EVAL_SEARCH_HEADER,
    EVAL_REPORT_HEADER,
    settings,
)
from deep_research.core.utils import with_retry, safe_extract_json

logger = logging.getLogger(__name__)

# Max chars of search results included in eval prompt. Must be large enough to
# fit the FULL collection the writer was grounded in — otherwise the evaluator
# penalises claims it cannot see the evidence for, producing false
# "unverifiable data" flags. Claude Sonnet 4.6 and Gemini 2.0 Flash both handle
# 200k+ context, so 60k chars is safe headroom.
_MAX_SEARCH_CHARS = 60_000


def _build_eval_prompt(report: str, search_results: str) -> str:
    """Combine base prompt + search results + report.

    If the evidence exceeds the cap, log a warning — under-truncation here is
    the #1 cause of false "unverifiable" flags in the quality assessment.
    """
    if len(search_results) > _MAX_SEARCH_CHARS:
        logger.warning(
            "Evaluator search_results truncated: %d > %d chars — "
            "accuracy scoring may miss evidence for later claims.",
            len(search_results), _MAX_SEARCH_CHARS,
        )
        search_results = search_results[:_MAX_SEARCH_CHARS] + "\n[...truncated for length...]"
    return (
        EVAL_PROMPT_TEMPLATE
        + EVAL_SEARCH_HEADER
        + search_results
        + EVAL_REPORT_HEADER
        + report
    )


@with_retry(retries=4, base_wait=2.0)
def _claude_evaluate_sync(report: str, search_results: str) -> dict:
    prompt = _build_eval_prompt(report, search_results)
    response = claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return safe_extract_json(response.content[0].text)


def claude_evaluate(report: str, search_results: str) -> dict:
    return _claude_evaluate_sync(report, search_results)


async def gemini_evaluate(report: str, search_results: str) -> dict:
    prompt = _build_eval_prompt(report, search_results)
    response = await gemini_client.chat.completions.create(
        model="gemini-2.0-flash",
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return safe_extract_json(response.choices[0].message.content)


async def consensus_evaluation(
    report: str,
    search_results: str = "",
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """
    Run Claude + Gemini in parallel with shared search evidence.
    Returns merged consensus dict including per-model reasoning and
    a disagreement_note when the score gap is large.
    """
    _emit(on_progress, "Evaluating report (Claude + Gemini in parallel)…")

    gemini_task = asyncio.create_task(gemini_evaluate(report, search_results))
    claude_task = asyncio.create_task(
        asyncio.to_thread(claude_evaluate, report, search_results)
    )
    gemini_fb, claude_fb = await asyncio.gather(gemini_task, claude_task)

    c_score: int = claude_fb.get("score", 0)
    g_score: int = gemini_fb.get("score", 0)
    c_reasoning: str = claude_fb.get("reasoning", "No reasoning provided.")
    g_reasoning: str = gemini_fb.get("reasoning", "No reasoning provided.")
    gap = abs(c_score - g_score)
    gap_threshold = settings.pipeline_score_gap_threshold

    disagreement_note = ""
    if gap >= gap_threshold:
        score = min(c_score, g_score)
        disagreement_note = (
            f"Claude ({c_score}/10): {c_reasoning}\n"
            f"Gemini ({g_score}/10): {g_reasoning}"
        )
        _emit(on_progress,
              f"  Claude: {c_score}  Gemini: {g_score}  Gap={gap} ⚠ large disagreement → min: {score}")
        _emit(on_progress, f"  Claude: {c_reasoning}")
        _emit(on_progress, f"  Gemini: {g_reasoning}")
    else:
        score = (c_score + g_score) // 2
        _emit(on_progress, f"  Claude: {c_score}  Gemini: {g_score}  Avg: {score}")

    cap = settings.pipeline_search_query_cap
    merged_queries: list[str] = list(
        set(claude_fb.get("search_queries", []) + gemini_fb.get("search_queries", []))
    )[:cap]
    # Merge weak sections from both models; trust the evaluator to return
    # actual section headings from the report (not invented ones)
    merged_weak: list[str] = list(
        set(claude_fb.get("weak_sections", []) + gemini_fb.get("weak_sections", []))
    )

    _emit(on_progress, f"  Score: {score} | Weak: {merged_weak}")

    return {
        "score": score,
        "needs_more_search": (
            claude_fb.get("needs_more_search", False)
            or gemini_fb.get("needs_more_search", False)
        ),
        "search_queries": merged_queries,
        "weak_sections": merged_weak,
        "rewrite_instructions": claude_fb.get("rewrite_instructions", {}),
        "claude_score": c_score,
        "gemini_score": g_score,
        "claude_reasoning": c_reasoning,
        "gemini_reasoning": g_reasoning,
        "disagreement_note": disagreement_note,
    }


def _emit(cb, msg):
    logger.info(msg)
    if cb:
        cb(msg)