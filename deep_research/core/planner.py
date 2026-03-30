"""
planner.py
──────────
Pipeline stages 2 & 3: query planning and parallel web search.

Agents in this file:
┌─────────────────┬──────────────────────────────────────────────────────────┐
│ PlannerAgent    │ Role:   Given an (already-rewritten) query, produce N    │
│                 │         typed search terms covering different dimensions. │
│                 │ Input:  Rewritten research query string.                  │
│                 │ Output: WebSearchPlan (list of WebSearchItem).            │
│                 │ Model:  gpt-4o-mini (fast, cheap; planning is low-risk).  │
├─────────────────┼──────────────────────────────────────────────────────────┤
│ SearchAgent     │ Role:   Execute one web search and summarise results.     │
│                 │ Input:  WebSearchItem (query + reason).                   │
│                 │ Output: Plain text summary (~300 words).                  │
│                 │ Model:  gpt-4o-mini + WebSearchTool.                      │
│                 │ Note:   Run N instances in parallel via asyncio.gather.   │
└─────────────────┴──────────────────────────────────────────────────────────┘

Key change from v1:
    perform_searches() and targeted_search() now return SearchDocumentCollection
    instead of list[str]. Each SearchDocument carries the originating query,
    enabling the Analyst Agent to score and deduplicate by provenance.
    Callers (writer.py, pipeline.py) call .to_eval_string() when they need
    a flat string for LLM context — no LLM prompt changes needed.
"""

import asyncio
import logging
from typing import Callable

from pydantic import BaseModel, Field
from agents import Agent, WebSearchTool, Runner
from agents.model_settings import ModelSettings

from deep_research.core.config import settings
from deep_research.core.search_documents import SearchDocument, SearchDocumentCollection

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────────────────────

class WebSearchItem(BaseModel):
    reason: str = Field(description="Why this search is important to the query.")
    query: str = Field(description="The search term to use.")


class WebSearchPlan(BaseModel):
    searches: list[WebSearchItem] = Field(description="List of web searches to perform.")


# ── Agent factories ────────────────────────────────────────────────────────────

def _build_planner_agent() -> Agent:
    n = settings.pipeline_how_many_searches
    return Agent(
        name="PlannerAgent",
        instructions=(
            f"You are a research planning assistant. Given a query, produce exactly {n} "
            "targeted web search terms that together give comprehensive coverage.\n\n"
            "Design your searches to cover different dimensions:\n"
            "- At least one broad overview search\n"
            "- At least one specific technical or mechanistic search\n"
            "- At least one data/statistics/quantitative search\n"
            "- At least one recent trends or forward-looking search\n\n"
            "Focus on empirical data, authoritative sources, and diverse perspectives."
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
            "and sources. Write succinctly — this feeds a report synthesiser."
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
    n_searches: int | None = None,
) -> WebSearchPlan:
    """
    Ask PlannerAgent to generate a typed set of search queries.

    Input:
        query:      Rewritten research query string.
        n_searches: Override the default search count from settings.
                    Used by pipeline.py to give expanded queries a larger
                    search budget — expanded queries cover more dimensions
                    and need proportionally more evidence.
    Output: WebSearchPlan with N WebSearchItems.
    """
    n = n_searches or settings.pipeline_how_many_searches
    _emit(on_progress, f"Planning searches (n={n})…")

    # Build a one-off instruction if n differs from the singleton's baked-in value
    if n != settings.pipeline_how_many_searches:
        from agents import Agent
        agent = Agent(
            name="PlannerAgent",
            instructions=(
                f"You are a research planning assistant. Given a query, produce exactly {n} "
                "targeted web search terms that together give comprehensive coverage.\n\n"
                "Design your searches to cover different dimensions:\n"
                "- At least one broad overview search\n"
                "- At least one specific technical or mechanistic search\n"
                "- At least one data/statistics/quantitative search\n"
                "- At least one recent trends or forward-looking search\n\n"
                "Focus on empirical data, authoritative sources, and diverse perspectives."
            ),
            model="gpt-4o-mini",
            output_type=WebSearchPlan,
        )
    else:
        agent = get_planner_agent()

    result = await Runner.run(agent, f"Query: {query}")
    plan: WebSearchPlan = result.final_output

    _emit(on_progress, f"→ {len(plan.searches)} searches planned:")
    for i, item in enumerate(plan.searches, 1):
        _emit(on_progress, f"   {i}. \"{item.query}\"")

    return plan


async def _run_single_search_with_progress(
    item: WebSearchItem,
    index: int,
    total: int,
    on_progress: Callable[[str], None] | None,
) -> SearchDocument:
    """
    Execute one search, emit progress, return a SearchDocument.

    Returns SearchDocument (not plain str) so the Analyst Agent can
    deduplicate and score by content + query provenance.
    """
    _emit(on_progress, f"   [{index}/{total}] Searching: \"{item.query}\"…")
    inp = f"Search term: {item.query}\nReason: {item.reason}"
    result = await Runner.run(get_search_agent(), inp)
    summary = str(result.final_output)
    _emit(on_progress, f"   [{index}/{total}] ✓ Done: \"{item.query}\"")
    return SearchDocument(content=summary, query=item.query)


async def run_single_search(item: WebSearchItem) -> SearchDocument:
    """Execute one search with no progress callback. Returns SearchDocument."""
    inp = f"Search term: {item.query}\nReason: {item.reason}"
    result = await Runner.run(get_search_agent(), inp)
    return SearchDocument(content=str(result.final_output), query=item.query)


async def perform_searches(
    search_plan: WebSearchPlan,
    on_progress: Callable[[str], None] | None = None,
) -> SearchDocumentCollection:
    """
    Fan out all searches in parallel; collect into a SearchDocumentCollection.

    Input:  WebSearchPlan
    Output: SearchDocumentCollection (unscored; relevance defaults to 1.0)
            → passed to Analyst Agent for dedup + scoring before writing.
    """
    total = len(search_plan.searches)
    _emit(on_progress, f"Running {total} searches in parallel…")

    tasks = [
        asyncio.create_task(
            _run_single_search_with_progress(item, i, total, on_progress)
        )
        for i, item in enumerate(search_plan.searches, 1)
    ]
    docs: list[SearchDocument] = await asyncio.gather(*tasks)
    collection = SearchDocumentCollection(documents=list(docs))

    stats = collection.to_summary_stats()
    _emit(on_progress, f"→ All searches complete ({stats['doc_count']} docs, {stats['total_chars']:,} chars)")
    return collection


async def targeted_search(
    queries: list[str],
    on_progress: Callable[[str], None] | None = None,
) -> SearchDocumentCollection:
    """
    Run a capped set of targeted searches during iterative refinement.

    Input:  list of query strings from evaluator feedback.
    Output: SearchDocumentCollection of new evidence docs.
            Caller (pipeline.py) merges this into the existing collection.
    """
    if not queries:
        return SearchDocumentCollection()

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
    docs: list[SearchDocument] = await asyncio.gather(*tasks)
    collection = SearchDocumentCollection(documents=list(docs))

    stats = collection.to_summary_stats()
    _emit(on_progress, f"→ Evidence retrieved ({stats['total_chars']:,} chars)")
    return collection


# ── Helper ─────────────────────────────────────────────────────────────────────

def _emit(cb: Callable[[str], None] | None, msg: str) -> None:
    logger.info(msg)
    if cb:
        cb(msg)