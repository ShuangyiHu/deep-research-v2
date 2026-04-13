"""
test_a2a_serialization.py
─────────────────────────
Verifies that SearchDocument / SearchDocumentCollection serialise to dict
and back without data loss. These round-trips are the foundation for all
A2A DataPart transport between pipeline and agents.
"""
import pytest

from deep_research.core.search_documents import SearchDocument, SearchDocumentCollection
from deep_research.a2a.schemas import (
    AnalyseInput,
    AnalyseOutput,
    EvaluateInput,
    EvaluateOutput,
    SearchDocumentData,
    SearchInput,
    SearchItemData,
    SearchOutput,
    WriteInput,
    WriteOutput,
)


# ── SearchDocument round-trip ─────────────────────────────────────────────────

def test_search_document_to_dict_preserves_all_fields():
    doc = SearchDocument(content="some text", query="AI jobs", relevance=0.75)
    d = doc.to_dict()
    assert d["content"] == "some text"
    assert d["query"] == "AI jobs"
    assert d["relevance"] == 0.75
    assert len(d["doc_id"]) == 12  # MD5 prefix


def test_search_document_from_dict_round_trip():
    original = SearchDocument(content="hello world", query="test query", relevance=0.5)
    restored = SearchDocument.from_dict(original.to_dict())
    assert restored.content == original.content
    assert restored.query == original.query
    assert restored.relevance == original.relevance
    assert restored.doc_id == original.doc_id


def test_search_document_from_dict_defaults():
    """Partial dict (no relevance / doc_id) should apply safe defaults."""
    doc = SearchDocument.from_dict({"content": "abc", "query": "q"})
    assert doc.relevance == 1.0
    assert doc.doc_id != ""  # __post_init__ computes it


# ── SearchDocumentCollection round-trip ───────────────────────────────────────

def test_collection_to_dict_round_trip():
    docs = [
        SearchDocument(content=f"doc {i}", query=f"query {i}", relevance=0.1 * i)
        for i in range(3)
    ]
    col = SearchDocumentCollection(documents=docs)
    restored = SearchDocumentCollection.from_dict(col.to_dict())

    assert len(restored.documents) == 3
    for orig, rest in zip(col.documents, restored.documents):
        assert rest.content == orig.content
        assert rest.query == orig.query
        assert abs(rest.relevance - orig.relevance) < 1e-9


def test_collection_from_dict_empty():
    col = SearchDocumentCollection.from_dict({})
    assert len(col.documents) == 0


def test_collection_to_dict_then_eval_string_unchanged():
    """to_eval_string() output must be identical before and after a round-trip."""
    docs = [SearchDocument(content="content A", query="q1"), SearchDocument(content="content B", query="q2")]
    col = SearchDocumentCollection(documents=docs)
    original_eval = col.to_eval_string()

    restored = SearchDocumentCollection.from_dict(col.to_dict())
    assert restored.to_eval_string() == original_eval


# ── A2A schema validation ─────────────────────────────────────────────────────

def test_search_input_schema():
    inp = SearchInput(items=[SearchItemData(query="AI news", reason="overview")])
    d = inp.model_dump()
    assert d["items"][0]["query"] == "AI news"
    restored = SearchInput.model_validate(d)
    assert restored.items[0].reason == "overview"


def test_analyse_input_schema():
    doc_data = SearchDocumentData(content="text", query="q", relevance=0.8, doc_id="abc")
    inp = AnalyseInput(documents=[doc_data], query="research topic")
    restored = AnalyseInput.model_validate(inp.model_dump())
    assert restored.query == "research topic"
    assert restored.documents[0].relevance == 0.8


def test_write_input_schema():
    doc_data = SearchDocumentData(content="evidence", query="q")
    inp = WriteInput(query="How does X work?", documents=[doc_data])
    restored = WriteInput.model_validate(inp.model_dump())
    assert restored.query == "How does X work?"


def test_evaluate_input_defaults():
    inp = EvaluateInput(report="# Report", search_results="evidence", query="q")
    assert inp.search_budget_remaining == 0


def test_evaluate_output_optional_evidence():
    out = EvaluateOutput(
        score=7,
        claude_score=7,
        gemini_score=7,
        collected_evidence=None,
    )
    d = out.model_dump()
    assert d["collected_evidence"] is None
    assert d["budget_consumed"] == 0


def test_evaluate_output_with_evidence():
    evidence = [SearchDocumentData(content="new fact", query="targeted q")]
    out = EvaluateOutput(
        score=6,
        claude_score=6,
        gemini_score=6,
        collected_evidence=evidence,
        budget_consumed=1,
    )
    d = out.model_dump()
    assert len(d["collected_evidence"]) == 1
    assert d["collected_evidence"][0]["content"] == "new fact"
    assert d["budget_consumed"] == 1


# ── SearchDocumentData ↔ SearchDocument bridging ─────────────────────────────

def test_schema_to_search_document_bridge():
    """SearchDocumentData.model_dump() feeds directly into SearchDocument.from_dict()."""
    schema_doc = SearchDocumentData(content="bridged", query="bridge q", relevance=0.6)
    core_doc = SearchDocument.from_dict(schema_doc.model_dump())
    assert core_doc.content == "bridged"
    assert core_doc.relevance == 0.6
    # Round-trip the other way
    back = SearchDocumentData(**core_doc.to_dict())
    assert back.content == "bridged"
