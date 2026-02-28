"""
test_pipeline_real.py
─────────────────────
端到端测试：真实 API key，真实网络请求，完整 pipeline。

什么是 e2e 测试：
  不 mock 任何东西，从用户视角验证整个系统端到端地工作。
  就像真人打开浏览器输入 query 点击生成一样，只是用代码模拟。

为什么平时不跑：
  1. 慢：一次完整 pipeline 需要 3-8 分钟
  2. 花钱：每次调用 Claude、Gemini、GPT-4o 都产生实际费用
  3. 不稳定：网络抖动、API 限速都可能让测试失败，不是代码的问题

什么时候跑：
  - 上线前的冒烟测试（smoke test）
  - 重大重构后验证整体功能没有断
  - 本地手动确认

运行方式：
  pytest tests/e2e/ -v -m e2e

需要：
  - .env 文件里有真实的 OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY
  - （可选）Redis 运行中（如果测试 Celery task 的话）
"""

import pytest
from deep_research.core.pipeline import run_pipeline


# ── pytest mark 注册 ───────────────────────────────────────────────────────────
# pytest.ini 或 conftest.py 里需要注册这个 mark，否则 pytest 会警告
# 在 pytest.ini 里加: markers = e2e: end-to-end tests requiring real API keys

pytestmark = pytest.mark.e2e


# ── 测试用的轻量 query ────────────────────────────────────────────────────────
# 故意选简单、答案清晰的 query，让 pipeline 容易达到质量阈值，跑得快一点。

SIMPLE_QUERY = (
    "What were the three main causes of the 2008 global financial crisis? "
    "Focus on mortgage-backed securities, regulatory failures, and leverage ratios. "
    "Summarize each cause with supporting evidence."
)


# ── e2e 测试 ───────────────────────────────────────────────────────────────────

@pytest.mark.e2e
def test_pipeline_returns_nonempty_markdown_report():
    """
    完整 pipeline 应该返回一个非空的 markdown 报告。
    这是最基本的冒烟测试 — 确认整条链路没有崩溃。
    """
    report = run_pipeline(
        query=SIMPLE_QUERY,
        threshold=6,    # 低一点，让测试更快完成
        max_iter=2,     # 最多跑两轮
    )

    assert isinstance(report, str)
    assert len(report) > 500, "Report is suspiciously short"


@pytest.mark.e2e
def test_pipeline_report_contains_markdown_structure():
    """报告应该有 markdown 标题（## 开头），说明 writer agent 正确生成了结构。"""
    report = run_pipeline(
        query=SIMPLE_QUERY,
        threshold=6,
        max_iter=2,
    )

    assert "##" in report, "Report has no markdown headings"


@pytest.mark.e2e
def test_pipeline_progress_callback_fires():
    """
    on_progress 回调应该在 pipeline 过程中被多次调用。
    验证进度汇报机制没有断掉。
    """
    messages = []

    run_pipeline(
        query=SIMPLE_QUERY,
        threshold=6,
        max_iter=1,
        on_progress=messages.append,
    )

    assert len(messages) >= 5, f"Expected ≥5 progress messages, got {len(messages)}: {messages}"

    # 应该有 PHASE 1 和 PHASE 2 的标记
    all_text = "\n".join(messages)
    assert "PHASE 1" in all_text or "Draft" in all_text
    assert "Iteration" in all_text or "evaluation" in all_text.lower()


@pytest.mark.e2e
def test_pipeline_score_is_reasonable():
    """
    让 pipeline 只跑一轮，检查 evaluator 给出的分数在合理范围内（1-10）。
    如果两个模型都返回了格式错误的 JSON 导致 fallback，分数会是 3。
    """
    scores_seen = []

    def capture_score(msg: str):
        # 从 progress log 里解析分数，格式如 "Score: 7 | Weak: [...]"
        if "Score:" in msg:
            try:
                score_part = msg.split("Score:")[1].split("|")[0].strip()
                scores_seen.append(int(score_part))
            except (ValueError, IndexError):
                pass

    run_pipeline(
        query=SIMPLE_QUERY,
        threshold=10,   # 故意设高，让它跑完所有 max_iter
        max_iter=1,
        on_progress=capture_score,
    )

    assert len(scores_seen) > 0, "No scores captured from progress log"
    for score in scores_seen:
        assert 1 <= score <= 10, f"Score out of range: {score}"
