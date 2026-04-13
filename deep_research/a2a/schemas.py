"""
a2a/schemas.py
──────────────
Pydantic models for A2A DataPart input/output payloads.

These define the typed contract between pipeline.py (caller) and each
AgentExecutor (server). Every call_agent() sends a DataPart containing
{"skill": "<skill_name>", "input": <InputModel.model_dump()>}
and expects a DataPart response containing <OutputModel.model_dump()>.

Why separate from core/ data models:
    - SearchDocumentCollection uses dataclasses optimised for in-process use.
    - These schemas are JSON-serialisable Pydantic models for wire transport.
    - to_dict()/from_dict() on SearchDocument bridges the two worlds.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


# ── Shared ────────────────────────────────────────────────────────────────────

class SearchDocumentData(BaseModel):
    """Wire representation of a single SearchDocument."""
    content: str
    query: str
    relevance: float = 1.0
    doc_id: str = ""


# ── Search agent ──────────────────────────────────────────────────────────────

class SearchItemData(BaseModel):
    query: str
    reason: str


class SearchInput(BaseModel):
    """
    Batch input: one call handles N search items in parallel inside the executor.
    Pipeline side: call_agent("search", "execute", SearchInput(items=[...]).model_dump())
    """
    items: list[SearchItemData]


class SearchOutput(BaseModel):
    documents: list[SearchDocumentData]


# ── Analyst agent ─────────────────────────────────────────────────────────────

class AnalyseInput(BaseModel):
    """
    Raw (unscored) documents + the rewritten query used for TF-IDF scoring.
    Pipeline side: call_agent("analyst", "analyse", AnalyseInput(...).model_dump())
    """
    documents: list[SearchDocumentData]
    query: str


class AnalyseOutput(BaseModel):
    """Deduplicated, relevance-scored, sorted documents."""
    documents: list[SearchDocumentData]


# ── Writer agent ──────────────────────────────────────────────────────────────

class WriteInput(BaseModel):
    """
    Scored evidence collection + rewritten query → draft markdown report.
    Pipeline side: call_agent("writer", "draft", WriteInput(...).model_dump())
    """
    query: str
    documents: list[SearchDocumentData]


class WriteOutput(BaseModel):
    report: str  # complete markdown report


# ── Evaluator agent ───────────────────────────────────────────────────────────

class EvaluateInput(BaseModel):
    """
    Report + flat evidence string (collection.to_eval_string()) + budget.
    search_budget_remaining: how many targeted search rounds the evaluator
      may initiate autonomously (agent-initiated flow).
    """
    report: str
    search_results: str        # pre-built from collection.to_eval_string()
    query: str                 # needed for Analyst re-scoring inside executor
    search_budget_remaining: int = Field(default=0)


class EvaluateOutput(BaseModel):
    score: int
    weak_sections: list[str] = Field(default_factory=list)
    needs_more_search: bool = False
    search_queries: list[str] = Field(default_factory=list)
    rewrite_instructions: dict[str, Any] = Field(default_factory=dict)
    claude_score: int = 0
    gemini_score: int = 0
    claude_reasoning: str = ""
    gemini_reasoning: str = ""
    disagreement_note: str = ""
    # Agent-initiated supplemental evidence (filled by EvaluatorExecutor)
    collected_evidence: list[SearchDocumentData] | None = None
    budget_consumed: int = 0
