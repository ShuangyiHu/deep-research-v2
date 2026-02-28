"""
test_pipeline.py
────────────────
单元测试：core/pipeline.py 的控制流逻辑

核心问题：给定不同的 evaluator 分数序列，pipeline 是否做出了正确的决定？
  - 分数达到阈值时是否提前停止？
  - 分数下降时是否回滚到最佳版本（FIX 1）？
  - max_iter 是否被遵守？

所有 draft_report / consensus_evaluation / rewrite_sections / targeted_search
都被 mock 掉，只测 pipeline.py 自己的逻辑。
"""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from deep_research.core.pipeline import iterative_loop


def _run(coro):
    return asyncio.run(coro)


def _make_feedback(score, weak=None, needs_search=False, queries=None):
    return {
        "score": score,
        "weak_sections": weak or ["Introduction"],
        "needs_more_search": needs_search,
        "search_queries": queries or [],
        "rewrite_instructions": {"Introduction": "Improve it."},
    }


class TestIterativeLoop:

    # ── 提前停止 ───────────────────────────────────────────────────────────────

    def test_stops_when_threshold_reached(self):
        """第一次 evaluation 就达到阈值，应该只跑一次，不触发 rewrite。"""
        feedback = _make_feedback(score=9)

        with patch("deep_research.core.pipeline.consensus_evaluation",
                   new_callable=AsyncMock, return_value=feedback) as mock_eval, \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock) as mock_rewrite:

            result = _run(iterative_loop("initial report", threshold=8, max_iter=4))

        assert mock_eval.call_count == 1
        mock_rewrite.assert_not_called()
        assert result == "initial report"

    def test_respects_max_iter(self):
        """分数始终不够，但不能超过 max_iter 次 rewrite。"""
        feedback = _make_feedback(score=5)  # 始终低于阈值 8

        with patch("deep_research.core.pipeline.consensus_evaluation",
                   new_callable=AsyncMock, return_value=feedback), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, return_value="rewritten report") as mock_rewrite:

            _run(iterative_loop("initial report", threshold=8, max_iter=3))

        # max_iter=3 → 最多 rewrite 3 次（最后一次 eval 后如果还没达到就停止）
        assert mock_rewrite.call_count <= 3

    # ── FIX 1: 回滚机制 ────────────────────────────────────────────────────────

    def test_keeps_best_report_when_score_improves(self):
        """分数逐步提升，最终返回最高分时对应的报告。"""
        # 第一次 eval: 5分 → 记录 "initial report" 为 best
        # rewrite → "report v2"
        # 第二次 eval: 8分 ≥ threshold → 停止，返回 "report v2"
        feedbacks = [
            _make_feedback(score=5),
            _make_feedback(score=8),
        ]
        rewrites = ["report v2"]

        with patch("deep_research.core.pipeline.consensus_evaluation",
                   new_callable=AsyncMock, side_effect=feedbacks), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, side_effect=rewrites):

            result = _run(iterative_loop("initial report", threshold=8, max_iter=4))

        assert result == "report v2"

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
        # consensus_evaluation 被调用的顺序：
        # call 1: initial report → 5
        # call 2: report v2 → 8
        # call 3: report v3 → 6  (触发回滚)
        # call 4: report v2 再次评估 → 8  (回滚后重新 eval)
        feedbacks = [
            _make_feedback(score=5),   # iter1: initial
            _make_feedback(score=8),   # iter2: v2  → new best
            _make_feedback(score=6),   # iter3: v3  → regression!
            _make_feedback(score=8),   # iter3 revert: re-eval v2 → stop
        ]
        rewrites = ["report v2", "report v3"]

        with patch("deep_research.core.pipeline.consensus_evaluation",
                   new_callable=AsyncMock, side_effect=feedbacks), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, side_effect=rewrites):

            result = _run(iterative_loop("initial report", threshold=8, max_iter=4))

        # 最终结果应该是回滚后的最佳版本，不是分数更低的 v3
        assert result == "report v2"

    # ── targeted_search: 搜索次数上限 ─────────────────────────────────────────

    def test_targeted_search_is_called_when_needed(self):
        """needs_more_search=True 且搜索次数未超上限时，应该触发 targeted_search。"""
        feedback = _make_feedback(score=5, needs_search=True, queries=["some query"])

        with patch("deep_research.core.pipeline.consensus_evaluation",
                   new_callable=AsyncMock, return_value=feedback), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, return_value="rewritten"), \
             patch("deep_research.core.pipeline.targeted_search",
                   new_callable=AsyncMock, return_value="extra evidence") as mock_search:

            _run(iterative_loop("initial", threshold=10, max_iter=1))

        mock_search.assert_called_once()

    def test_targeted_search_not_called_when_no_queries(self):
        """search_queries 为空时，即使 needs_more_search=True 也不搜索。"""
        feedback = _make_feedback(score=5, needs_search=True, queries=[])

        with patch("deep_research.core.pipeline.consensus_evaluation",
                   new_callable=AsyncMock, return_value=feedback), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, return_value="rewritten"), \
             patch("deep_research.core.pipeline.targeted_search",
                   new_callable=AsyncMock) as mock_search:

            _run(iterative_loop("initial", threshold=10, max_iter=1))

        mock_search.assert_not_called()

    # ── on_progress 回调 ──────────────────────────────────────────────────────

    def test_on_progress_receives_iteration_messages(self):
        """pipeline 应该通过 on_progress 汇报迭代进度。"""
        feedback_low  = _make_feedback(score=5)
        feedback_high = _make_feedback(score=9)
        messages = []

        with patch("deep_research.core.pipeline.consensus_evaluation",
                   new_callable=AsyncMock, side_effect=[feedback_low, feedback_high]), \
             patch("deep_research.core.pipeline.rewrite_sections",
                   new_callable=AsyncMock, return_value="rewritten"):

            _run(iterative_loop(
                "initial", threshold=8, max_iter=4,
                on_progress=messages.append,
            ))

        # 应该有包含 "Iteration" 的消息
        iter_msgs = [m for m in messages if "Iteration" in m or "iter" in m.lower()]
        assert len(iter_msgs) > 0
