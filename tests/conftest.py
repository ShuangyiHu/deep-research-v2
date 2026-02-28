"""
conftest.py
───────────
Shared pytest fixtures and configuration.

conftest.py 是 pytest 的特殊文件 — pytest 启动时自动加载它，
里面定义的 fixture 所有测试文件都能直接用，不需要 import。

Fixtures 是 pytest 的依赖注入系统：
    def test_something(sample_report):  # ← pytest 自动把 fixture 的返回值注入进来
        assert len(sample_report) > 0
"""

import pytest


# ── 共享测试数据 ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_report() -> str:
    """一份最小化的合法 markdown 报告，供多个测试复用。"""
    return """# Research Report

## Introduction
This report examines AI impacts on software engineering hiring.

## Task Automation vs Job Elimination
AI tools automate specific tasks like boilerplate code generation,
not entire job roles. Studies show 30% of coding tasks are automatable
but this does not translate to 30% job losses.

## Short-run vs Long-run Demand
In 2025-2026, hiring freezes are expected at large tech firms.
By 2027-2030, new roles in AI supervision are projected to grow.

## Productivity vs Hiring Demand
Productivity gains of 40% (GitHub Copilot studies) do not linearly
reduce headcount — firms often reinvest gains into new products.

## Capital-Labor Substitution
The elasticity of substitution between AI tools and junior developers
is estimated at 0.6, meaning they are partial substitutes.

## Scale Effects
Market expansion in AI-adjacent products is creating new categories
of software roles that did not exist in 2023.

## Junior Engineer Pipeline Risk
Reduced entry-level hiring today risks a senior engineer shortage
in 5-10 years, as there will be fewer developers gaining experience.

## Complementary Skills
Skills that remain valuable: system design, debugging complex systems,
stakeholder communication, and AI prompt engineering.

## Conclusion
The net effect on junior developer hiring is negative in the short run
but ambiguous in the long run, dependent on scale effects.
"""


@pytest.fixture
def minimal_feedback() -> dict:
    """최소화된 evaluator feedback dict."""
    return {
        "score": 6,
        "weak_sections": ["Complementary Skills", "Scale Effects"],
        "needs_more_search": True,
        "search_queries": ["AI junior developer hiring 2025", "AI coding productivity studies"],
        "rewrite_instructions": {
            "Complementary Skills": "Add specific skill names with salary or demand data.",
            "Scale Effects": "Cite concrete examples of new job categories created by AI.",
        },
    }


@pytest.fixture
def high_score_feedback() -> dict:
    """Score above threshold — should stop the loop."""
    return {
        "score": 9,
        "weak_sections": [],
        "needs_more_search": False,
        "search_queries": [],
        "rewrite_instructions": {},
    }


@pytest.fixture
def low_score_feedback() -> dict:
    """Score below threshold — should trigger rewrite."""
    return {
        "score": 3,
        "weak_sections": ["Introduction", "Capital-Labor Substitution"],
        "needs_more_search": True,
        "search_queries": ["AI labor substitution elasticity 2024"],
        "rewrite_instructions": {
            "Introduction": "Add a stronger framing of the research question.",
            "Capital-Labor Substitution": "Define elasticity and cite a study.",
        },
    }
