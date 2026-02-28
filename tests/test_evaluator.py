"""
test_evaluator.py
─────────────────
单元测试：core/evaluator.py 的合并逻辑

注意：我们不测试"Claude/Gemini 有没有返回好的评分"——
那是模型的事，不是我们代码的事。
我们测试的是：给定两个模型的输出，我们的合并逻辑是否正确。

所有 claude_evaluate / gemini_evaluate 调用都被 mock 掉。
"""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock

from deep_research.core.evaluator import consensus_evaluation
from deep_research.core.config import CANONICAL_SECTIONS


class TestConsensusEvaluation:

    def _run(self, coro):
        """Helper: 在测试里同步运行 async 函数。"""
        return asyncio.run(coro)

    def _make_feedback(self, score, weak=None, queries=None, needs_search=False):
        return {
            "score": score,
            "weak_sections": weak or [],
            "search_queries": queries or [],
            "needs_more_search": needs_search,
            "rewrite_instructions": {},
        }

    # ── FIX 4: 分数差距大时取最小值 ──────────────────────────────────────────

    def test_uses_min_score_when_gap_is_large(self):
        """Claude=9, Gemini=3 → gap=6 ≥ 4, 取 min=3."""
        claude_fb = self._make_feedback(score=9)
        gemini_fb = self._make_feedback(score=3)

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        assert result["score"] == 3

    def test_uses_average_when_gap_is_small(self):
        """Claude=7, Gemini=8 → gap=1 < 4, 取平均=7 (整除)."""
        claude_fb = self._make_feedback(score=7)
        gemini_fb = self._make_feedback(score=8)

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        assert result["score"] == 7  # (7 + 8) // 2

    def test_gap_exactly_at_threshold_uses_min(self):
        """gap=4 正好等于阈值，应该用 min。"""
        claude_fb = self._make_feedback(score=8)
        gemini_fb = self._make_feedback(score=4)

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        assert result["score"] == 4

    # ── FIX 2: 合并后的搜索 query 数量上限 ───────────────────────────────────

    def test_merged_queries_capped_at_five(self):
        """两个模型各提供 4 个 query，合并后不超过 5 个。"""
        claude_fb = self._make_feedback(
            score=5,
            queries=["query A", "query B", "query C", "query D"],
        )
        gemini_fb = self._make_feedback(
            score=5,
            queries=["query E", "query F", "query G", "query H"],
        )

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        assert len(result["search_queries"]) <= 5

    def test_duplicate_queries_are_deduplicated(self):
        """两个模型提供同样的 query，合并后只保留一个。"""
        same_queries = ["AI hiring 2025", "junior developer demand"]
        claude_fb = self._make_feedback(score=5, queries=same_queries)
        gemini_fb = self._make_feedback(score=5, queries=same_queries)

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        # 去重后还是那两个
        assert len(result["search_queries"]) == 2

    # ── FIX 3: weak_sections 只保留 canonical 名字 ───────────────────────────

    def test_non_canonical_sections_are_filtered_out(self):
        """模型返回了不在 CANONICAL_SECTIONS 里的名字，应该被过滤掉。"""
        claude_fb = self._make_feedback(
            score=5,
            weak=["Introduction", "Some Random Section Name"],  # 第二个不合法
        )
        gemini_fb = self._make_feedback(
            score=5,
            weak=["Complementary Skills", "Another Fake Section"],  # 第二个不合法
        )

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        for section in result["weak_sections"]:
            assert section in CANONICAL_SECTIONS

    def test_all_canonical_sections_are_kept(self):
        """合法的 section 名字不应该被过滤。"""
        valid_sections = ["Introduction", "Complementary Skills", "Conclusion"]
        claude_fb = self._make_feedback(score=5, weak=valid_sections)
        gemini_fb = self._make_feedback(score=5, weak=[])

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        for section in valid_sections:
            assert section in result["weak_sections"]

    # ── needs_more_search: OR 逻辑 ────────────────────────────────────────────

    def test_needs_more_search_is_true_if_either_says_so(self):
        claude_fb = self._make_feedback(score=6, needs_search=True)
        gemini_fb = self._make_feedback(score=6, needs_search=False)

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        assert result["needs_more_search"] is True

    def test_needs_more_search_is_false_if_both_say_no(self):
        claude_fb = self._make_feedback(score=8, needs_search=False)
        gemini_fb = self._make_feedback(score=8, needs_search=False)

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        assert result["needs_more_search"] is False

    # ── rewrite_instructions 来源 ─────────────────────────────────────────────

    def test_rewrite_instructions_come_from_claude(self):
        """rewrite_instructions 应该用 Claude 的（更详细）。"""
        claude_instructions = {"Introduction": "Claude's detailed instruction."}
        gemini_instructions = {"Introduction": "Gemini's instruction."}

        claude_fb = self._make_feedback(score=5)
        claude_fb["rewrite_instructions"] = claude_instructions

        gemini_fb = self._make_feedback(score=5)
        gemini_fb["rewrite_instructions"] = gemini_instructions

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            result = self._run(consensus_evaluation("fake report"))

        assert result["rewrite_instructions"] == claude_instructions

    # ── on_progress 回调 ──────────────────────────────────────────────────────

    def test_on_progress_callback_is_called(self):
        """on_progress 应该在 evaluation 过程中被调用至少一次。"""
        claude_fb = self._make_feedback(score=7)
        gemini_fb = self._make_feedback(score=7)
        progress_messages = []

        with patch("deep_research.core.evaluator.claude_evaluate", return_value=claude_fb), \
             patch("deep_research.core.evaluator.gemini_evaluate", new_callable=AsyncMock, return_value=gemini_fb):
            self._run(
                consensus_evaluation("fake report", on_progress=progress_messages.append)
            )

        assert len(progress_messages) > 0
