"""
writer.py
─────────
Pipeline stage 4: plan → search → [analyse] → draft.

Agent in this file:
┌──────────────┬───────────────────────────────────────────────────────────────┐
│ WriterAgent  │ Role:   Given a query and scored, deduplicated search evidence,│
│              │         produce a structured research report in markdown.      │
│              │ Input:  Rewritten query + context string from                  │
│              │         SearchDocumentCollection (post-Analyst).               │
│              │ Output: ReportData (summary + full markdown + follow-ups).     │
│              │ Model:  gpt-4o-mini.                                           │
└──────────────┴───────────────────────────────────────────────────────────────┘

Key change from v1:
    draft_report() now accepts an optional pre-built SearchDocumentCollection
    (produced by the Analyst Agent upstream). If provided, it skips the search
    phase and uses the already-structured evidence directly.

    Return type is still (markdown_report: str, collection: SearchDocumentCollection)
    so pipeline.py can pass the collection forward to the evaluator and the
    iterative refinement loop — the evaluator calls .to_eval_string() itself.
"""

import logging
from typing import Callable

from pydantic import BaseModel, Field
from agents import Agent, Runner

from deep_research.core.planner import plan_searches, perform_searches
from deep_research.core.search_documents import SearchDocumentCollection

logger = logging.getLogger(__name__)


# ── Output schema ──────────────────────────────────────────────────────────────

class ReportData(BaseModel):
    short_summary: str = Field(description="A 2-3 sentence summary of the findings.")
    markdown_report: str = Field(description="The full report in markdown, 1000+ words.")
    follow_up_questions: list[str] = Field(description="Topics to research further.")


# ── Agent factory ──────────────────────────────────────────────────────────────

def _build_writer_agent() -> Agent:
    return Agent(
        name="WriterAgent",
        instructions=(
            "You are a senior research analyst. Write a detailed, well-structured "
            "report in markdown based on the query and search results provided.\n\n"
            "Requirements:\n"
            "- Design section headings specific to THIS query — no generic templates\n"
            "- Minimum 1000 words\n"
            "- Use ## for section headings\n"
            "- Back every major claim with data, statistics, or named sources from the search results\n"
            "- Include an Introduction and a Conclusion\n"
            "- Body sections should reflect the actual sub-questions and themes in the query\n"
            "- Write for an informed reader; avoid padding\n\n"
            "Each search result is prefixed with [Source query: ...] — use this to "
            "attribute claims to specific search directions when relevant."
        ),
        model="gpt-4o-mini",
        output_type=ReportData,
    )


_writer_agent: Agent | None = None


def get_writer_agent() -> Agent:
    global _writer_agent
    if _writer_agent is None:
        _writer_agent = _build_writer_agent()
    return _writer_agent


# ── Public function ────────────────────────────────────────────────────────────

async def draft_report(
    query: str,
    on_progress: Callable[[str], None] | None = None,
    collection: SearchDocumentCollection | None = None,
    n_searches: int | None = None,
) -> tuple[str, SearchDocumentCollection]:
    """
    Draft a research report from a query.

    Input:
        query:      The (rewritten) research question.
        collection: Pre-built SearchDocumentCollection from Analyst Agent.
                    If None, this function runs search internally.
        n_searches: Override search count. Pipeline passes a higher value
                    when the query was expanded by QueryRewriterAgent, to
                    match the increased dimensional scope of the rewritten query.

    Output:
        (markdown_report, collection)
    """
    _emit(on_progress, "─── PHASE 1: Drafting report ───")

    if collection is None:
        _emit(on_progress, "[writer] No collection supplied — running search internally")
        search_plan = await plan_searches(query, on_progress=on_progress, n_searches=n_searches)
        collection = await perform_searches(search_plan, on_progress=on_progress)

    context_string = collection.to_eval_string()
    inp = f"Original query: {query}\n\nSearch results:\n{context_string}"

    _emit(on_progress, "Writing draft report…")
    result = await Runner.run(get_writer_agent(), inp)
    report_data: ReportData = result.final_output

    _emit(on_progress, "→ Draft complete")
    return report_data.markdown_report, collection


# ── Helper ─────────────────────────────────────────────────────────────────────

def _emit(cb, msg):
    logger.info(msg)
    if cb:
        cb(msg)