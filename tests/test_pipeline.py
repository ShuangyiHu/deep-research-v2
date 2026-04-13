"""
test_pipeline.py
────────────────
单元测试：core/pipeline.py 的控制流逻辑

核心问题：给定不同的 evaluator 分数序列，pipeline 是否做出了正确的决定？
  - 分数达到阈值时是否提前停止？
  - 分数下降时是否回滚到最佳版本（FIX 1）？
  - max_iter 是否被遵守？

Phase F update:
  consensus_evaluation 已迁移进 EvaluatorExecutor，pipeline 现在调用
  evaluator_evaluate (A2A client)。所有测试 mock 已从 consensus_evaluation
  切换到 evaluator_evaluate。

  targeted_search / search_execute 也已迁移进 EvaluatorExecutor —— pipeline
  的 iterative_loop 不再直接调用它们。测试 5-6 改为验证 evaluator 返回的
  collected_evidence 是否被正确合并到 collection。
"""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from deep_research.core.pipeline import iterative_loop
from deep_research.core.search_documents import SearchDocumentCollection


def _run(coro):
    return asyncio.run(coro)


def _empty_collection() -> SearchDocumentCollection:
    return SearchDocumentCollection(documents=[])


def _make_feedback(score, weak=None, needs_search=False, queries=None,
                   collected_evidence=None, budget_consumed=0):
    return {
        "score": score,
        "weak_sections": weak or ["Introduction"],
        "needs_more_search": needs_search,
        "search_queries": queries or [],
        "rewrite_instructions": {"Introduction": "Improve it."},
        "collected_evidence": collected_evidence,
        "budget_consumed": budget_consumed,
    }


class TestIterativeLoop:

    # ── 提前停止 ────────────────────────────────────────────────────��──────────

    def test_stops_when_threshold_reached(self):
        """第一次 evaluation 就达到阈值，应该只跑一次，不触发 rewrite。"""
        feedback = _make_feedback(score=9)

        with patch("deep_research.core.pipeline.evaluator_evaluate",
                   new_callable=AsyncMock, return_value=feedback) as mock_eval, \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock) as mock_rewrite:

            report, _, _ = _run(
                iterative_loop("initial report", _empty_collection(), "test query",
                               threshold=8, max_iter=4)
            )

        assert mock_eval.call_count == 1
        mock_rewrite.assert_not_called()
        assert report == "initial report"

    def test_respects_max_iter(self):
        """分数始终不够，但不能超过 max_iter 次 rewrite。"""
        feedback = _make_feedback(score=5)  # 始终低于阈值 8

        with patch("deep_research.core.pipeline.evaluator_evaluate",
                   new_callable=AsyncMock, return_value=feedback), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, return_value="rewritten report") as mock_rewrite:

            _run(iterative_loop("initial report", _empty_collection(), "test query",
                                threshold=8, max_iter=3))

        # max_iter=3 → 最多 rewrite 3 次（最后一次 eval 后如果还没达到就停止）
        assert mock_rewrite.call_count <= 3

    # ── FIX 1: 回滚机制 ────────────────────────────────────────────────────────

    def test_keeps_best_report_when_score_improves(self):
        """分数逐步提升，最终返回最高分时对应的报告。"""
        feedbacks = [
            _make_feedback(score=5),
            _make_feedback(score=8),
        ]
        rewrites = ["report v2"]

        with patch("deep_research.core.pipeline.evaluator_evaluate",
                   new_callable=AsyncMock, side_effect=feedbacks), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, side_effect=rewrites):

            report, _, _ = _run(
                iterative_loop("initial report", _empty_collection(), "test query",
                               threshold=8, max_iter=4)
            )

        assert report == "report v2"

    def test_reverts_to_best_when_score_regresses(self):
        """
        分数先升后降 → 应该回滚到最高分版本。

        序列:
          iter 1: eval("initial") = 5  → best="initial", score=5
                  rewrite → "report v2"
          iter 2: eval("report v2") = 8  → best="report v2", score=8
                  rewrite → "report v3"
          iter 3: eval("report v3") = 6  < best_score=8 → 回滚到 "report v2"
                  re-eval("report v2") = 8  ≥ threshold → 停止
        """
        feedbacks = [
            _make_feedback(score=5),   # iter1: initial
            _make_feedback(score=8),   # iter2: v2  → new best
            _make_feedback(score=6),   # iter3: v3  → regression!
            _make_feedback(score=8),   # iter3 revert: re-eval v2 → stop
        ]
        rewrites = ["report v2", "report v3"]

        with patch("deep_research.core.pipeline.evaluator_evaluate",
                   new_callable=AsyncMock, side_effect=feedbacks), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, side_effect=rewrites):

            report, _, _ = _run(
                iterative_loop("initial report", _empty_collection(), "test query",
                               threshold=8, max_iter=4)
            )

        assert report == "report v2"

    # ── Phase F: evaluator 返回的 collected_evidence 合并 ──────────────────────

    def test_merges_evidence_returned_by_evaluator(self):
        """
        EvaluatorExecutor 完成 agent-initiated 搜索后，在 feedback 中返回
        collected_evidence。pipeline 应把这些 doc 合并进 collection，并把
        它们作为 new_evidence (kwarg) 传给 rewrite_sections。full_evidence
        应始终是完整的 collection（反幻觉关键）。

        序列:
          iter 1: evaluator 返回 score=5 且 collected_evidence=[doc1]
                  → pipeline 合并 doc1 → rewrite
          iter 2: evaluator 返回 score=9 → 达到阈值停止
        """
        doc1 = {"content": "new evidence", "query": "q1", "relevance": 0.9, "doc_id": "d1"}
        feedback_with_evidence = _make_feedback(
            score=5, collected_evidence=[doc1], budget_consumed=1
        )
        feedback_high = _make_feedback(score=9)

        with patch("deep_research.core.pipeline.evaluator_evaluate",
                   new_callable=AsyncMock,
                   side_effect=[feedback_with_evidence, feedback_high]), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, return_value="report v2") as mock_rewrite:

            report, _, _ = _run(
                iterative_loop("initial", _empty_collection(), "q",
                               threshold=8, max_iter=4)
            )

        assert mock_rewrite.call_count == 1
        call_kwargs = mock_rewrite.call_args_list[0].kwargs
        # new_evidence kwarg should contain the agent-initiated supplemental docs
        assert call_kwargs["new_evidence"] is not None
        assert "new evidence" in call_kwargs["new_evidence"]
        # full_evidence kwarg should also contain the supplemental docs (merged into collection)
        assert "new evidence" in call_kwargs["full_evidence"]

    def test_no_merge_when_evaluator_returns_no_evidence(self):
        """
        EvaluatorExecutor 未触发补搜（returned collected_evidence=None），
        pipeline 不应合并任何额外文档：new_evidence=None，但 full_evidence
        仍然是（空的）collection 字符串——rewriter 必须看到完整证据以避免幻觉。
        """
        feedback = _make_feedback(score=5, collected_evidence=None, budget_consumed=0)

        with patch("deep_research.core.pipeline.evaluator_evaluate",
                   new_callable=AsyncMock, return_value=feedback), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, return_value="rewritten") as mock_rewrite:

            _run(iterative_loop("initial", _empty_collection(), "q",
                                threshold=10, max_iter=1))

        call_kwargs = mock_rewrite.call_args_list[0].kwargs
        assert call_kwargs["new_evidence"] is None
        # full_evidence is always passed (empty string for empty collection)
        assert "full_evidence" in call_kwargs

    # ── on_progress 回调 ─────────────────────────────────────────────────��────

    def test_on_progress_receives_iteration_messages(self):
        """pipeline 应该通过 on_progress 汇报迭代进度。"""
        feedback_low  = _make_feedback(score=5)
        feedback_high = _make_feedback(score=9)
        messages = []

        with patch("deep_research.core.pipeline.evaluator_evaluate",
                   new_callable=AsyncMock, side_effect=[feedback_low, feedback_high]), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, return_value="rewritten"):

            _run(iterative_loop(
                "initial", _empty_collection(), "q",
                threshold=8, max_iter=4,
                on_progress=messages.append,
            ))

        # 应该有包含 "Iteration" 的消息
        iter_msgs = [m for m in messages if "Iteration" in m or "iter" in m.lower()]
        assert len(iter_msgs) > 0
