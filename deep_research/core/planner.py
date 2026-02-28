"""
planner.py
──────────
Step 1 of the pipeline:
  - PlannerAgent  → generates N targeted search queries for a user query
  - SearchAgent   → executes a single web search and returns a summary
  - plan_searches()    → async, calls PlannerAgent
  - perform_searches() → async, fans out searches and reports each completion
  - targeted_search()  → async, used during iterative refinement
"""

import asyncio
import logging
from typing import Callable

from pydantic import BaseModel, Field
from agents import Agent, WebSearchTool, Runner
from agents.model_settings import ModelSettings

from deep_research.core.config import settings

logger = logging.getLogger(__name__)

# ── Data models ────────────────────────────────────────────────────────────────

class WebSearchItem(BaseModel):
    reason: str = Field(description="Why this search is important to the query.")
    query: str = Field(description="The search term to use.")


class WebSearchPlan(BaseModel):
    searches: list[WebSearchItem] = Field(description="List of web searches to perform.")


# ── Agents ─────────────────────────────────────────────────────────────────────

def _build_planner_agent() -> Agent:
    n = settings.pipeline_how_many_searches
    return Agent(
        name="PlannerAgent",
        instructions=(
            f"You are a research assistant. Given a query, produce {n} "
            "targeted web search terms to best answer it. Focus on empirical data, "
            "labor market trends, and skills forecasts."
        ),
        model="gpt-4o-mini",
        output_type=WebSearchPlan,
    )


def _build_search_agent() -> Agent:
    return Agent(
        name="SearchAgent",
        instructions=(
            "You are a research assistant. Search the web for the given term and produce "
            "a concise 2-3 paragraph summary (<300 words). Capture key facts, statistics, "
            "and sources. Write succinctly — this feeds a report synthesizer."
        ),
        tools=[WebSearchTool(search_context_size="low")],
        model="gpt-4o-mini",
        model_settings=ModelSettings(tool_choice="required"),
    )


_planner_agent: Agent | None = None
_search_agent: Agent | None = None


def get_planner_agent() -> Agent:
    global _planner_agent
    if _planner_agent is None:
        _planner_agent = _build_planner_agent()
    return _planner_agent


def get_search_agent() -> Agent:
    global _search_agent
    if _search_agent is None:
        _search_agent = _build_search_agent()
    return _search_agent


# ── Public async functions ─────────────────────────────────────────────────────

async def plan_searches(
    query: str,
    on_progress: Callable[[str], None] | None = None,
) -> WebSearchPlan:
    """Ask the PlannerAgent to generate a set of search queries."""
    _emit(on_progress, "Planning searches…")
    result = await Runner.run(get_planner_agent(), f"Query: {query}")
    plan: WebSearchPlan = result.final_output

    # Show all planned keywords so the user knows what will be searched
    _emit(on_progress, f"→ {len(plan.searches)} searches planned:")
    for i, item in enumerate(plan.searches, 1):
        _emit(on_progress, f"   {i}. \"{item.query}\"")

    return plan


async def _run_single_search_with_progress(
    item: WebSearchItem,
    index: int,
    total: int,
    on_progress: Callable[[str], None] | None,
) -> str:
    """Execute one search, emit start + done events, return summary."""
    _emit(on_progress, f"   [{index}/{total}] Searching: \"{item.query}\"…")
    inp = f"Search term: {item.query}\nReason: {item.reason}"
    result = await Runner.run(get_search_agent(), inp)
    _emit(on_progress, f"   [{index}/{total}] ✓ Done: \"{item.query}\"")
    return str(result.final_output)


async def run_single_search(item: WebSearchItem) -> str:
    """Execute one search (no progress callback). Used internally."""
    inp = f"Search term: {item.query}\nReason: {item.reason}"
    result = await Runner.run(get_search_agent(), inp)
    return str(result.final_output)


async def perform_searches(
    search_plan: WebSearchPlan,
    on_progress: Callable[[str], None] | None = None,
) -> list[str]:
    """Fan out all searches in parallel; report each as it completes."""
    total = len(search_plan.searches)
    _emit(on_progress, f"Running {total} searches in parallel…")

    tasks = [
        asyncio.create_task(
            _run_single_search_with_progress(item, i, total, on_progress)
        )
        for i, item in enumerate(search_plan.searches, 1)
    ]
    results = await asyncio.gather(*tasks)
    _emit(on_progress, "→ All searches complete")
    return list(results)


async def targeted_search(
    queries: list[str],
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """Run a capped set of targeted searches during iterative refinement."""
    if not queries:
        return ""
    cap = settings.pipeline_search_query_cap
    capped = queries[:cap]
    total = len(capped)
    _emit(on_progress, f"Running {total} targeted searches…")

    items = [WebSearchItem(reason="Targeted evidence retrieval", query=q) for q in capped]
    tasks = [
        asyncio.create_task(
            _run_single_search_with_progress(item, i, total, on_progress)
        )
        for i, item in enumerate(items, 1)
    ]
    results = await asyncio.gather(*tasks)
    evidence = "\n\n".join(r for r in results if r)
    _emit(on_progress, f"→ Evidence retrieved ({len(evidence):,} chars)")
    return evidence


# ── Helper ─────────────────────────────────────────────────────────────────────

def _emit(cb: Callable[[str], None] | None, msg: str) -> None:
    logger.info(msg)
    if cb:
        cb(msg)