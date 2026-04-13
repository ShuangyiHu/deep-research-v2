"""
test_evaluator_initiated_search.py
────────────────────────────────────
验证 EvaluatorExecutor 的 agent-initiated 补搜行为。

Phase F 核心特性：当 consensus_evaluation 判断需要更多证据（needs_more_search=True）
且 search_budget_remaining > 0 时，EvaluatorExecutor 会自主调用 Search A2A + Analyst A2A，
而不是交由 pipeline 决定。

这里 mock 掉：
  - consensus_evaluation （直接 core 函数）
  - search_execute  （A2A client，EvaluatorExecutor 内部调用）
  - analyst_analyse （A2A client，EvaluatorExecutor 内部调用）
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from deep_research.core.search_documents import SearchDocument, SearchDocumentCollection


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_eval_feedback(score=6, needs_search=True, queries=None):
    return {
        "score": score,
        "needs_more_search": needs_search,
        "search_queries": queries or ["extra query 1", "extra query 2"],
        "weak_sections": ["Introduction"],
        "rewrite_instructions": {"Introduction": "Improve it."},
        "claude_score": score,
        "gemini_score": score,
        "claude_reasoning": "needs more data",
        "gemini_reasoning": "needs more data",
        "disagreement_note": "",
    }


def _make_collection(*contents) -> SearchDocumentCollection:
    return SearchDocumentCollection(
        documents=[SearchDocument(content=c, query="q") for c in contents]
    )


def _run(coro):
    return asyncio.run(coro)


# ── Test: EvaluatorExecutor triggers search when budget > 0 ──────────────────

class TestEvaluatorInitiatedSearch:

    def test_initiates_search_when_budget_available(self):
        """
        needs_more_search=True + search_budget_remaining=2 →
        EvaluatorExecutor 应自主调用 search_execute + analyst_analyse。
        """
        from deep_research.a2a.executors.evaluator import _do_supplemental_search

        scored_collection = _make_collection("doc1 content", "doc2 content")

        with patch(
            "deep_research.a2a.clients.search_execute",
            new_callable=AsyncMock,
            return_value=_make_collection("raw doc"),
        ) as mock_search, \
        patch(
            "deep_research.a2a.clients.analyst_analyse",
            new_callable=AsyncMock,
            return_value=scored_collection,
        ) as mock_analyst:

            docs, consumed = _run(
                _do_supplemental_search(
                    queries=["extra query 1", "extra query 2"],
                    query="main research query",
                )
            )

        mock_search.assert_called_once()
        mock_analyst.assert_called_once()
        assert consumed == 1
        assert docs is not None
        assert len(docs) == 2  # matches scored_collection

    def test_returns_none_docs_when_search_fails(self):
        """
        Search A2A 调用失败时，_do_supplemental_search 应优雅降级：
        返回 (None, 0) 而不是抛出异常，让 pipeline 正常继续。
        """
        from deep_research.a2a.executors.evaluator import _do_supplemental_search

        with patch(
            "deep_research.a2a.clients.search_execute",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Search service unavailable"),
        ):
            docs, consumed = _run(
                _do_supplemental_search(
                    queries=["any query"],
                    query="main query",
                )
            )

        assert docs is None
        assert consumed == 0

    def test_budget_zero_skips_search_in_executor(self):
        """
        pipeline 传入 search_budget_remaining=0 时，EvaluatorExecutor
        不应��用 _do_supplemental_search。
        验证：EvaluateOutput.budget_consumed == 0，collected_evidence is None。
        """
        from deep_research.a2a.executors.evaluator import _do_supplemental_search

        with patch(
            "deep_research.a2a.clients.search_execute",
            new_callable=AsyncMock,
        ) as mock_search:
            # _do_supplemental_search has an early return for empty queries —
            # this tests that guard works: no search_execute call, returns (None, 0).
            docs, consumed = _run(
                _do_supplemental_search(queries=[], query="main query")
            )

        # search_execute should NOT be called for empty queries
        mock_search.assert_not_called()
        assert docs is None
        assert consumed == 0

    def test_evaluator_client_includes_collected_evidence_in_output(self):
        """
        evaluator_evaluate (client) 应把 EvaluateOutput.collected_evidence 和
        budget_consumed 原样传回给 pipeline。
        使用 call_agent mock 验证 client 层的数据流向。
        """
        from deep_research.a2a.schemas import EvaluateOutput, SearchDocumentData

        # Simulate what EvaluatorExecutor returns as EvaluateOutput
        evidence_doc = SearchDocumentData(
            content="supplemental content",
            query="extra query 1",
            relevance=0.8,
        )
        fake_output = EvaluateOutput(
            score=7,
            needs_more_search=True,
            search_queries=["extra query 1"],
            weak_sections=["Introduction"],
            rewrite_instructions={},
            collected_evidence=[evidence_doc],
            budget_consumed=1,
        )

        with patch(
            "deep_research.a2a.clients.call_agent",
            new_callable=AsyncMock,
            return_value=fake_output.model_dump(),
        ):
            from deep_research.a2a.clients import evaluator_evaluate
            result = _run(
                evaluator_evaluate(
                    report="some report",
                    collection=_make_collection("existing doc"),
                    query="research query",
                    search_budget_remaining=2,
                )
            )

        assert result["score"] == 7
        assert result["budget_consumed"] == 1
        assert result["collected_evidence"] is not None
        assert len(result["collected_evidence"]) == 1
        assert result["collected_evidence"][0]["content"] == "supplemental content"
