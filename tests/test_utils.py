"""
test_utils.py
─────────────
单元测试：core/utils.py

测什么：
  - safe_extract_json 的各种输入情况
  - with_retry 装饰器的重试逻辑

为什么这些测试不需要 API key：
  utils.py 里没有任何网络调用，全是纯 Python 逻辑。
"""

import pytest
import time
from unittest.mock import MagicMock, patch

from deep_research.core.utils import safe_extract_json, with_retry


# ── safe_extract_json ──────────────────────────────────────────────────────────

class TestSafeExtractJson:

    def test_parses_clean_json(self):
        raw = '{"score": 7, "weak_sections": ["Introduction"]}'
        result = safe_extract_json(raw)
        assert result["score"] == 7
        assert result["weak_sections"] == ["Introduction"]

    def test_strips_json_markdown_fence(self):
        raw = '```json\n{"score": 8}\n```'
        result = safe_extract_json(raw)
        assert result["score"] == 8

    def test_strips_plain_markdown_fence(self):
        # 模型有时候只写 ``` 不写 json
        raw = '```\n{"score": 5}\n```'
        result = safe_extract_json(raw)
        assert result["score"] == 5

    def test_extracts_json_from_surrounding_text(self):
        # 模型在 JSON 前后加了废话
        raw = 'Here is my evaluation:\n{"score": 6}\nHope this helps!'
        result = safe_extract_json(raw)
        assert result["score"] == 6

    def test_returns_fallback_on_invalid_json(self):
        result = safe_extract_json("this is not json at all")
        # fallback dict 必须包含这几个 key
        assert "score" in result
        assert "weak_sections" in result
        assert "needs_more_search" in result

    def test_returns_fallback_on_empty_string(self):
        result = safe_extract_json("")
        assert "score" in result

    def test_handles_nested_json(self):
        raw = '{"score": 7, "rewrite_instructions": {"Intro": "Add more context."}}'
        result = safe_extract_json(raw)
        assert result["rewrite_instructions"]["Intro"] == "Add more context."

    def test_fallback_score_is_low(self):
        # fallback score 应该偏低，这样 pipeline 会继续尝试改进
        result = safe_extract_json("not json")
        assert result["score"] <= 5


# ── with_retry ─────────────────────────────────────────────────────────────────

class TestWithRetry:

    def test_returns_value_on_first_success(self):
        mock_fn = MagicMock(return_value="success")

        @with_retry(retries=3)
        def fn():
            return mock_fn()

        result = fn()
        assert result == "success"
        assert mock_fn.call_count == 1

    def test_retries_on_retryable_error(self):
        # 前两次抛 rate limit 错误，第三次成功
        mock_fn = MagicMock(
            side_effect=[
                Exception("rate limit exceeded"),
                Exception("rate limit exceeded"),
                "success",
            ]
        )

        @with_retry(retries=4, base_wait=0)  # base_wait=0 让测试不要真的等待
        def fn():
            return mock_fn()

        result = fn()
        assert result == "success"
        assert mock_fn.call_count == 3

    def test_raises_immediately_on_non_retryable_error(self):
        # ValueError 不在 RETRYABLE_ERRORS 里，应该立刻抛出，不重试
        mock_fn = MagicMock(side_effect=ValueError("bad input"))

        @with_retry(retries=4, base_wait=0)
        def fn():
            return mock_fn()

        with pytest.raises(ValueError, match="bad input"):
            fn()

        # 只调用了一次，没有重试
        assert mock_fn.call_count == 1

    def test_raises_after_all_retries_exhausted(self):
        mock_fn = MagicMock(side_effect=Exception("overloaded"))

        @with_retry(retries=3, base_wait=0)
        def fn():
            return mock_fn()

        with pytest.raises(Exception, match="overloaded"):
            fn()

        assert mock_fn.call_count == 3

    def test_preserves_function_name(self):
        # functools.wraps 要正确保留函数名，否则 logging 里会显示 "wrapper"
        @with_retry(retries=2)
        def my_specific_function():
            pass

        assert my_specific_function.__name__ == "my_specific_function"
