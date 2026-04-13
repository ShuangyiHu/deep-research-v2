"""
a2a/clients.py
──────────────
Typed convenience wrappers around call_agent().

These are optional — pipeline.py can use call_agent() directly.
They exist to:
  1. Provide IDE autocompletion and type checking on inputs/outputs.
  2. Handle SearchDocument ↔ SearchDocumentData conversion in one place.
  3. Document the exact skill name and schema for each agent.

Usage:
    from deep_research.a2a.clients import search_client, analyst_client

    docs = await search_client.execute(items=[WebSearchItem(...)])
    scored = await analyst_client.analyse(collection, query)
"""
from __future__ import annotations

from deep_research.a2a.invocation import call_agent
from deep_research.a2a.schemas import (
    AnalyseInput,
    AnalyseOutput,
    EvaluateInput,
    EvaluateOutput,
    SearchInput,
    SearchItemData,
    SearchOutput,
    WriteInput,
    WriteOutput,
)
from deep_research.core.search_documents import SearchDocument, SearchDocumentCollection


# ── Search client ─────────────────────────────────────────────────────────────

async def search_execute(items: list[SearchItemData]) -> SearchDocumentCollection:
    """Call SearchExecutor.execute skill — batch N items in one call."""
    payload = SearchInput(items=items).model_dump()
    raw = await call_agent("search", "execute", payload)
    out = SearchOutput.model_validate(raw)
    return SearchDocumentCollection(
        documents=[SearchDocument.from_dict(d.model_dump()) for d in out.documents]
    )


# ── Analyst client ────────────────────────────────────────────────────────────

async def analyst_analyse(
    collection: SearchDocumentCollection,
    query: str,
) -> SearchDocumentCollection:
    """Call AnalystExecutor.analyse skill."""
    from deep_research.a2a.schemas import SearchDocumentData
    payload = AnalyseInput(
        documents=[SearchDocumentData(**d.to_dict()) for d in collection.documents],
        query=query,
    ).model_dump()
    raw = await call_agent("analyst", "analyse", payload)
    out = AnalyseOutput.model_validate(raw)
    return SearchDocumentCollection(
        documents=[SearchDocument.from_dict(d.model_dump()) for d in out.documents]
    )


# ── Writer client ─────────────────────────────────────────────────────────────

async def writer_draft(
    query: str,
    collection: SearchDocumentCollection,
) -> str:
    """Call WriterExecutor.draft skill — returns markdown report string."""
    from deep_research.a2a.schemas import SearchDocumentData
    payload = WriteInput(
        query=query,
        documents=[SearchDocumentData(**d.to_dict()) for d in collection.documents],
    ).model_dump()
    raw = await call_agent("writer", "draft", payload)
    return WriteOutput.model_validate(raw).report


# ── Evaluator client ──────────────────────────────────────────────────────────

async def evaluator_evaluate(
    report: str,
    collection: SearchDocumentCollection,
    query: str,
    search_budget_remaining: int,
) -> dict:
    """
    Call EvaluatorExecutor.evaluate skill.

    Returns the raw EvaluateOutput dict so pipeline.py can access all fields
    (score, weak_sections, collected_evidence, budget_consumed, etc.)
    without needing to import EvaluateOutput itself.
    """
    payload = EvaluateInput(
        report=report,
        search_results=collection.to_eval_string(),
        query=query,
        search_budget_remaining=search_budget_remaining,
    ).model_dump()
    raw = await call_agent("evaluator", "evaluate", payload)
    return EvaluateOutput.model_validate(raw).model_dump()
