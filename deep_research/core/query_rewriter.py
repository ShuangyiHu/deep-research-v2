"""
query_rewriter.py
─────────────────
Pipeline stage 1: raw user input → well-formed research query.

Agent in this file:
┌──────────────────────┬──────────────────────────────────────────────────────┐
│ QueryRewriterAgent   │ Role:   First agent in the pipeline. Transforms any  │
│                      │         user input — including vague keywords — into  │
│                      │         a precise, multi-dimensional research query   │
│                      │         that downstream agents can work with.         │
│                      │                                                       │
│                      │ Input:  Raw user query string (any length/quality).   │
│                      │                                                       │
│                      │ Output: RewrittenQuery with:                          │
│                      │           - rewritten_query: the expanded string      │
│                      │           - dimensions_added: list of what was added  │
│                      │           - was_expanded: bool (for logging/metrics)  │
│                      │                                                       │
│                      │ Model:  gpt-4o-mini (fast; this is a cheap first step)│
│                      │                                                       │
│                      │ Why this agent:                                       │
│                      │   The rest of the pipeline (Planner, Writer,          │
│                      │   Evaluator) is bottlenecked by input quality.        │
│                      │   A vague query like "AI coding jobs" produces        │
│                      │   shallow, generic search plans and weak reports.     │
│                      │   Rewriting at the source — before any search —       │
│                      │   improves every downstream stage simultaneously.     │
│                      │                                                       │
│                      │ Design decision — when NOT to rewrite:                │
│                      │   If the user already supplied a detailed query       │
│                      │   (≥ 40 words with time/geography/data dimensions),   │
│                      │   the agent returns it unchanged. Over-rewriting a    │
│                      │   good query can introduce drift from user intent.    │
└──────────────────────┴──────────────────────────────────────────────────────┘
"""

import logging
from typing import Callable

from pydantic import BaseModel, Field
from agents import Agent, Runner

logger = logging.getLogger(__name__)

# Queries at or above this word count are considered already well-formed.
# Threshold chosen to match the UI example queries (~40–60 words).
_ALREADY_DETAILED_THRESHOLD = 40


# ── Output schema ──────────────────────────────────────────────────────────────

class RewrittenQuery(BaseModel):
    rewritten_query: str = Field(
        description=(
            "The expanded, well-formed research query. If the original was already "
            "detailed, return it unchanged."
        )
    )
    dimensions_added: list[str] = Field(
        description=(
            "Short labels for dimensions added during rewriting, e.g. "
            "['time range', 'geography', 'quantitative angle', 'mechanism']. "
            "Empty list if the query was already detailed and no changes were made."
        )
    )
    was_expanded: bool = Field(
        description="True if the query was rewritten/expanded, False if returned as-is."
    )


# ── Agent factory ──────────────────────────────────────────────────────────────

def _build_query_rewriter_agent() -> Agent:
    return Agent(
        name="QueryRewriterAgent",
        instructions=(
            "You are a research query specialist. Your job is to transform a user's "
            "raw input into a precise, multi-dimensional research question.\n\n"
            "A well-formed research query covers these dimensions:\n"
            "  1. Time scope      — when? (near-term, medium-term, or a horizon the user implies)\n"
            "  2. Geography       — where? (a market or region the user implies, or globally)\n"
            "  3. Causal/mechanism — what forces or mechanisms to investigate\n"
            "  4. Quantitative    — what data, metrics, or statistics to look for\n"
            "  5. Stakeholder     — who is affected or acting (e.g. 'workers', 'firms')\n\n"
            "Rules:\n"
            "  - If the input is already detailed (long, specific, multi-dimensional), "
            "return it UNCHANGED with was_expanded=false.\n"
            "  - Do NOT change the user's core topic or intent — only add missing dimensions.\n"
            "  - The rewritten query should be 40–80 words: complete but not bloated.\n"
            "  - Write it as a single well-formed research question, not a list.\n"
            "  - Populate dimensions_added with short labels for what you added.\n\n"
            "CRITICAL — time scope and geography:\n"
            "  - EXTRACT time scope and geography from the user's input if they are present.\n"
            "  - If the user gives NO time scope, use open-ended language such as "
            "'over the next several years' or 'in the near to medium term'. "
            "Do NOT invent specific years (e.g. '2024–2028') — you have no basis for them.\n"
            "  - If the user gives NO geography, use 'globally' or 'across major markets'. "
            "Do NOT assume a region (e.g. 'North America') unless the user implies it.\n\n"
            "Examples of good rewriting:\n"
            "  Input:  'AI coding jobs'\n"
            "  Output: 'How are AI-assisted coding tools expected to affect hiring demand "
            "for software engineers globally over the next several years, which roles face "
            "the greatest displacement risk, and which technical skills are projected to "
            "remain complementary to AI-driven development?'\n\n"
            "  Input:  'GLP-1 drugs market'\n"
            "  Output: 'How is the rapid commercialisation of GLP-1 weight-loss drugs "
            "reshaping the pharmaceutical market, healthcare spending, and consumer "
            "behaviour in the near to medium term, and which industry segments face "
            "the largest disruption?'\n\n"
            "  Input:  'AI coding jobs North America 2025 to 2030'\n"
            "  Output: 'How are AI-assisted coding tools expected to shift hiring demand "
            "for software engineers in North America between 2025 and 2030, which roles "
            "face displacement, and which skills remain complementary to AI-driven "
            "development?' "
            "(geography and time extracted directly from user input)"
        ),
        model="gpt-4o-mini",
        output_type=RewrittenQuery,
    )


_query_rewriter_agent: Agent | None = None


def get_query_rewriter_agent() -> Agent:
    global _query_rewriter_agent
    if _query_rewriter_agent is None:
        _query_rewriter_agent = _build_query_rewriter_agent()
    return _query_rewriter_agent


# ── Public function ────────────────────────────────────────────────────────────

async def rewrite_query(
    query: str,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[str, bool]:
    """
    Transform raw user input into a well-formed research query.

    Input:  Raw user query (any quality).
    Output: (rewritten_query, was_expanded)
            - rewritten_query: expanded string, or original if already detailed.
            - was_expanded: True only when the LLM actually rewrote the query.
              False for fast-path (already detailed) and for LLM no-op cases.
              Used by pipeline.py to allocate a larger search budget.
    """
    word_count = len(query.split())

    if word_count >= _ALREADY_DETAILED_THRESHOLD:
        _emit(on_progress, f"Query Rewriter: query already detailed ({word_count} words), skipping.")
        return query, False

    _emit(on_progress, f"Query Rewriter: expanding short query ({word_count} words)…")

    result = await Runner.run(get_query_rewriter_agent(), f"User query: {query}")
    rewritten: RewrittenQuery = result.final_output

    if rewritten.was_expanded:
        dims = ", ".join(rewritten.dimensions_added) if rewritten.dimensions_added else "none logged"
        _emit(on_progress, f"  Original:  {query}")
        _emit(on_progress, f"  Rewritten: {rewritten.rewritten_query}")
        _emit(on_progress, f"  Dimensions added: [{dims}]")
    else:
        _emit(on_progress, "  Query Rewriter: no expansion needed.")

    return rewritten.rewritten_query, rewritten.was_expanded


# ── Helper ─────────────────────────────────────────────────────────────────────

def _emit(cb: Callable[[str], None] | None, msg: str) -> None:
    logger.info(msg)
    if cb:
        cb(msg)