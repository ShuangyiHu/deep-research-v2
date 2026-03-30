"""
analysis.py
───────────
Pipeline stage 3.5: search results → structured, deduplicated, scored evidence.

Agent in this file:
┌───────────────┬──────────────────────────────────────────────────────────────┐
│ AnalystAgent  │ Role:   Sits between Search Agent and Writer Agent.          │
│               │         Cleans and structures raw search evidence before     │
│               │         it reaches the LLM writer.                           │
│               │                                                              │
│               │ Input:  SearchDocumentCollection (raw, unscored, may have    │
│               │         duplicates or off-topic results).                    │
│               │                                                              │
│               │ Output: SearchDocumentCollection (deduplicated, relevance-   │
│               │         scored, sorted, filtered above threshold).           │
│               │                                                              │
│               │ Why no LLM:                                                  │
│               │   Deduplication and basic relevance scoring are              │
│               │   deterministic tasks. Using an LLM here would add 10-20s   │
│               │   and API cost with no quality benefit. The Evaluator        │
│               │   (Claude + Gemini) is the right place for semantic          │
│               │   quality judgement — the Analyst handles evidence hygiene.  │
└───────────────┴──────────────────────────────────────────────────────────────┘

Three responsibilities:
  1. Deduplication   — exact + near-duplicate removal via content hashing
                       and Jaccard similarity on word shingles.
  2. Relevance score — lightweight TF-IDF-style keyword overlap between
                       each document and the original query. No LLM needed.
  3. Filtering       — drop docs below relevance threshold; sort survivors
                       descending by relevance so the Writer sees the best
                       evidence first.

Design decision — relevance threshold:
  Default threshold is 0.15 (permissive). The Analyst is not the quality
  gate — the Evaluator is. The Analyst's job is to remove clear noise
  (zero keyword overlap, near-identical duplicates), not to be aggressive.
  A threshold of 0.15 filters ~10-20% of docs in practice while never
  accidentally discarding niche-but-relevant results.
"""

import logging
import re
from typing import Callable

from deep_research.core.search_documents import SearchDocument, SearchDocumentCollection

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

# Minimum relevance score to keep a document (0.0–1.0).
# Permissive by design — the Evaluator is the quality gate, not the Analyst.
_RELEVANCE_THRESHOLD: float = 0.15

# Jaccard similarity above this → documents are near-duplicates; keep only first.
_DEDUP_SIMILARITY_THRESHOLD: float = 0.75

# Shingle size (words) for near-duplicate detection.
_SHINGLE_SIZE: int = 6


# ── Text helpers ──────────────────────────────────────────────────────────────

def _tokenise(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into word tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _shingles(tokens: list[str], k: int = _SHINGLE_SIZE) -> set[str]:
    """Return the set of k-word shingles from a token list."""
    if len(tokens) < k:
        return {" ".join(tokens)}
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two shingle sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _relevance_score(doc_tokens: list[str], query_tokens: list[str]) -> float:
    """
    Keyword overlap relevance: fraction of unique query terms found in the doc.

    Simple, fast, and good enough for evidence hygiene.
    Normalised to [0.0, 1.0] — score of 1.0 means every query keyword appears.

    Example:
        query = "AI coding tools junior engineers hiring 2025"
        doc contains 4/7 query terms → score ≈ 0.57
    """
    if not query_tokens:
        return 1.0  # No query tokens to match against; keep everything
    doc_set = set(doc_tokens)
    query_unique = set(query_tokens)
    # Remove very common stopwords that inflate scores artificially
    stopwords = {
        "the", "a", "an", "and", "or", "in", "of", "to", "is", "for",
        "are", "be", "was", "by", "on", "with", "that", "this", "it",
        "as", "at", "from", "how", "what", "which", "will",
    }
    query_meaningful = query_unique - stopwords
    if not query_meaningful:
        return 1.0
    matched = len(query_meaningful & doc_set)
    return round(matched / len(query_meaningful), 4)


# ── Core analysis logic ───────────────────────────────────────────────────────

def _deduplicate(
    documents: list[SearchDocument],
    on_progress: Callable[[str], None] | None = None,
) -> list[SearchDocument]:
    """
    Remove exact duplicates (by doc_id hash) and near-duplicates
    (Jaccard similarity above threshold on word shingles).

    Keeps the first occurrence of each near-duplicate cluster.
    Order is preserved for non-duplicates.
    """
    seen_ids: set[str] = set()
    seen_shingles: list[set[str]] = []
    kept: list[SearchDocument] = []
    dropped_exact = 0
    dropped_near = 0

    for doc in documents:
        # Exact duplicate check (content hash)
        if doc.doc_id in seen_ids:
            dropped_exact += 1
            continue
        seen_ids.add(doc.doc_id)

        # Near-duplicate check (shingle Jaccard)
        tokens = _tokenise(doc.content)
        doc_shingles = _shingles(tokens)
        is_near_dup = any(
            _jaccard(doc_shingles, prev) >= _DEDUP_SIMILARITY_THRESHOLD
            for prev in seen_shingles
        )
        if is_near_dup:
            dropped_near += 1
            continue

        seen_shingles.append(doc_shingles)
        kept.append(doc)

    total_dropped = dropped_exact + dropped_near
    if total_dropped > 0:
        _emit(
            on_progress,
            f"  Dedup: removed {total_dropped} docs "
            f"({dropped_exact} exact, {dropped_near} near-duplicate) "
            f"→ {len(kept)} remaining",
        )
    return kept


def _score_documents(
    documents: list[SearchDocument],
    query: str,
) -> list[SearchDocument]:
    """
    Compute and assign relevance scores in-place.
    Returns the same list with .relevance fields updated.
    """
    query_tokens = _tokenise(query)
    for doc in documents:
        doc_tokens = _tokenise(doc.content)
        doc.relevance = _relevance_score(doc_tokens, query_tokens)
    return documents


# ── Public interface ──────────────────────────────────────────────────────────

def analyse(
    collection: SearchDocumentCollection,
    query: str,
    relevance_threshold: float = _RELEVANCE_THRESHOLD,
    on_progress: Callable[[str], None] | None = None,
) -> SearchDocumentCollection:
    """
    AnalystAgent: clean, score, and filter a SearchDocumentCollection.

    Input:
        collection:          Raw SearchDocumentCollection from perform_searches().
        query:               The original (rewritten) research query — used for
                             relevance scoring keyword matching.
        relevance_threshold: Docs below this score are dropped (default 0.15).

    Output:
        SearchDocumentCollection — deduplicated, scored, sorted descending
        by relevance, filtered above threshold.

    Pipeline position:
        perform_searches() → analyse() → draft_report()

    Does NOT call any LLM. Pure Python. Typical runtime: <10ms.
    """
    docs = collection.documents
    input_count = len(docs)

    _emit(on_progress, f"─── Analyst Agent: {input_count} docs in ───")

    # Step 1: Deduplicate
    docs = _deduplicate(docs, on_progress=on_progress)

    # Step 2: Score relevance against query
    docs = _score_documents(docs, query)

    # Step 3: Filter below threshold
    before_filter = len(docs)
    docs = [d for d in docs if d.relevance >= relevance_threshold]
    filtered_out = before_filter - len(docs)
    if filtered_out > 0:
        _emit(on_progress, f"  Relevance filter: dropped {filtered_out} low-relevance docs")

    # Step 4: Sort descending by relevance so Writer sees best evidence first
    docs.sort(key=lambda d: d.relevance, reverse=True)

    result = SearchDocumentCollection(documents=docs)
    stats = result.to_summary_stats()
    _emit(
        on_progress,
        f"─── Analyst Agent: {stats['doc_count']} docs out "
        f"(avg relevance: {stats['avg_relevance']}) ───",
    )

    return result


# ── Helper ─────────────────────────────────────────────────────────────────────

def _emit(cb: Callable[[str], None] | None, msg: str) -> None:
    logger.info(msg)
    if cb:
        cb(msg)