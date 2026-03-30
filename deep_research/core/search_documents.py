"""
search_documents.py
───────────────────
Structured data models for search results.

Agent role: Data contract layer between Search Agent and downstream consumers
            (Analyst Agent, Writer Agent, Evaluator).

Before this module:
    search_results was a plain concatenated string: "\n\n---\n\n".join(summaries)
    — opaque, no metadata, impossible to filter or score.

After this module:
    search_results is list[SearchDocument], each carrying:
    - content:   the search summary text
    - query:     the search term that produced it
    - relevance: 0.0–1.0 score assigned by Analyst Agent (default 1.0 until scored)
    - doc_id:    stable hash for deduplication

Downstream compatibility:
    Call SearchDocument.to_context_string() for a single doc.
    Call SearchDocumentCollection.to_eval_string() to produce the flat string
    that evaluator.py and writer.py already expect — zero changes needed there.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SearchDocument:
    """
    A single structured search result.

    Role:    Atomic unit of evidence in the pipeline.
    Input:   Raw summary string from SearchAgent + the query that produced it.
    Output:  Structured object with relevance score and stable identity.

    Why structured:
        - Enables deduplication by content hash (Analyst Agent)
        - Enables relevance scoring and filtering (Analyst Agent)
        - Preserves query provenance for evaluator grounding
        - Makes pipeline data flow explicit and inspectable
    """

    content: str
    query: str
    relevance: float = 1.0          # Set by Analyst Agent; default = unscored
    doc_id: str = field(default="") # Stable MD5 of content; set on __post_init__

    def __post_init__(self) -> None:
        if not self.doc_id:
            self.doc_id = hashlib.md5(self.content.encode()).hexdigest()[:12]

    def to_context_string(self) -> str:
        """
        Render for inclusion in LLM context (Writer / Evaluator prompts).
        Preserves query provenance so the model can attribute claims.
        """
        return f"[Source query: {self.query}]\n{self.content}"


@dataclass
class SearchDocumentCollection:
    """
    An ordered, scored collection of SearchDocuments.

    Role:    Container passed between Search Agent → Analyst Agent → Writer Agent.
    Input:   list[SearchDocument] (raw from search, then scored by Analyst).
    Output:  Flat strings for LLM consumption, or filtered subsets.

    Why a collection class:
        - Centralises stringify logic (one place to change formatting)
        - Provides filter_by_relevance() for Analyst Agent output
        - to_eval_string() is a drop-in replacement for the old
          "\n\n---\n\n".join(search_results) pattern
    """

    documents: list[SearchDocument] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.documents)

    def filter_by_relevance(self, threshold: float = 0.3) -> "SearchDocumentCollection":
        """Return a new collection keeping only docs above the relevance threshold."""
        return SearchDocumentCollection(
            documents=[d for d in self.documents if d.relevance >= threshold]
        )

    def to_eval_string(self) -> str:
        """
        Flat string for Evaluator and Writer LLM prompts.
        Drop-in replacement for the old concatenated string format.
        Includes query provenance so the evaluator can ground accuracy claims.
        """
        return "\n\n---\n\n".join(d.to_context_string() for d in self.documents)

    def to_summary_stats(self) -> dict:
        """
        Metadata dict for logging and eval baseline metrics.
        Used by eval_baseline.py to track dedup_rate and doc counts.
        """
        return {
            "doc_count": len(self.documents),
            "total_chars": sum(len(d.content) for d in self.documents),
            "avg_relevance": (
                round(sum(d.relevance for d in self.documents) / len(self.documents), 3)
                if self.documents else 0.0
            ),
        }