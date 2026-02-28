"""
writer.py
─────────
Step 2: plan → search → draft.
Now returns (report, search_results) so the evaluator can fact-check
against the actual search evidence rather than training-data knowledge.
"""

import logging
from typing import Callable

from pydantic import BaseModel, Field
from agents import Agent, Runner

from deep_research.core.planner import plan_searches, perform_searches

logger = logging.getLogger(__name__)


class ReportData(BaseModel):
    short_summary: str = Field(description="A 2-3 sentence summary of the findings.")
    markdown_report: str = Field(description="The full report in markdown, 1000+ words.")
    follow_up_questions: list[str] = Field(description="Topics to research further.")


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
            "- Write for an informed reader; avoid padding"
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


async def draft_report(
    query: str,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """
    Returns (markdown_report, combined_search_results).
    The search results are passed to the evaluator for evidence-grounded fact-checking.
    """
    _emit(on_progress, "─── PHASE 1: Drafting report ───")
    search_plan = await plan_searches(query, on_progress=on_progress)
    search_results = await perform_searches(search_plan, on_progress=on_progress)

    combined_search = "\n\n---\n\n".join(search_results)
    inp = f"Original query: {query}\n\nSearch results:\n{combined_search}"

    _emit(on_progress, "Writing draft report…")
    result = await Runner.run(get_writer_agent(), inp)
    report_data: ReportData = result.final_output

    _emit(on_progress, "→ Draft complete")
    return report_data.markdown_report, combined_search


def _emit(cb, msg):
    logger.info(msg)
    if cb:
        cb(msg)