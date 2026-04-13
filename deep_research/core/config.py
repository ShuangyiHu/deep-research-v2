"""
config.py — environment variables and pipeline constants.
"""
"""
Pipeline agent map — where each agent lives:
 
  QueryRewriterAgent  → core/query_rewriter.py   Stage 1
  PlannerAgent        → core/planner.py           Stage 2
  SearchAgent         → core/planner.py           Stage 3  (N parallel)
  AnalystAgent        → core/analysis.py          Stage 3.5
  WriterAgent         → core/writer.py            Stage 4
  EvaluatorAgent      → core/evaluator.py         Stage 5  (Claude + Gemini parallel)
  RewriteAgent        → core/rewriter.py          Stage 6
  StructureAgent      → core/rewriter.py          Stage 6  (post-rewrite pass)
 
Iterative loop (stages 5–6) repeats up to PIPELINE_MAX_ITERATIONS times
or until consensus score ≥ PIPELINE_QUALITY_THRESHOLD.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    REDIS_URL: str = Field(..., alias="REDIS_URL")
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")
    google_api_key: str = Field(..., alias="GOOGLE_API_KEY")
    sendgrid_api_key: str = Field(..., alias="SENDGRID_API_KEY")

    sendgrid_from_email: str = Field(default="noreply@deepresearch.ai", alias="SENDGRID_FROM_EMAIL")
    sendgrid_from_name: str = Field(default="Deep Research", alias="SENDGRID_FROM_NAME")

    pipeline_quality_threshold: int = Field(default=8, alias="PIPELINE_QUALITY_THRESHOLD")
    pipeline_max_iterations: int = Field(default=4, alias="PIPELINE_MAX_ITERATIONS")
    pipeline_max_targeted_searches: int = Field(default=2, alias="PIPELINE_MAX_TARGETED_SEARCHES")
    pipeline_how_many_searches: int = Field(default=5, alias="PIPELINE_HOW_MANY_SEARCHES")
    pipeline_search_query_cap: int = Field(default=5, alias="PIPELINE_SEARCH_QUERY_CAP")
    pipeline_score_gap_threshold: int = Field(default=4, alias="PIPELINE_SCORE_GAP_THRESHOLD")

    # ── A2A agent endpoints (override per-agent for split-container deploys) ──
    a2a_base_url: str = Field(default="http://localhost:8000/a2a", alias="A2A_BASE_URL")
    a2a_search_url: str | None = Field(default=None, alias="A2A_SEARCH_URL")
    a2a_analyst_url: str | None = Field(default=None, alias="A2A_ANALYST_URL")
    a2a_writer_url: str | None = Field(default=None, alias="A2A_WRITER_URL")
    a2a_evaluator_url: str | None = Field(default=None, alias="A2A_EVALUATOR_URL")

    # ── MCP tool abstraction ───────────────────────────────────────────────────
    mcp_search_mode: str = Field(default="in-process", alias="MCP_SEARCH_MODE")


settings = Settings()

# ── Canonical section names ────────────────────────────────────────────────────
# Used by consensus_evaluation() to filter hallucinated section names returned
# by the evaluator models. Only sections whose headings appear in this set are
# kept in weak_sections — prevents the pipeline from rewriting a section that
# does not actually exist in the report.
CANONICAL_SECTIONS: frozenset[str] = frozenset({
    "Introduction",
    "Background",
    "Overview",
    "Summary",
    "Executive Summary",
    "Methodology",
    "Task Automation vs Job Elimination",
    "Short-run vs Long-run Demand",
    "Productivity vs Hiring Demand",
    "Capital-Labor Substitution",
    "Scale Effects",
    "Junior Engineer Pipeline Risk",
    "Complementary Skills",
    "Conclusion",
    "References",
    "Key Findings",
    "Analysis",
    "Discussion",
    "Recommendations",
    "Implications",
    "Market Dynamics",
    "Future Outlook",
})

# ── Evaluator prompt ───────────────────────────────────────────────────────────
# IMPORTANT: The evaluator receives both the report AND the original search
# results that the report was written from. Criterion 7 (Accuracy) must be
# evaluated against the PROVIDED search results — not the model's training data.
# This eliminates false-negative accuracy penalties for recent/niche topics
# that postdate the model's knowledge cutoff.

EVAL_PROMPT_TEMPLATE: str = """
You are an expert research reviewer. You will evaluate a report AND the search
results it was based on.

Return ONLY valid JSON with NO markdown fences.

{
  "score": <int 1-10>,
  "reasoning": "<2-3 sentences explaining your score — cite specific strengths and weaknesses>",
  "weak_sections": [<exact section heading from the report>, ...],
  "needs_more_search": <true|false>,
  "search_queries": [<str>, ...],
  "rewrite_instructions": {
    "<exact section heading>": {
      "missing_angles":       [<str>, ...],   // sub-topics the section does not cover
      "questions_to_answer":  [<str>, ...],   // specific questions the rewritten section should answer
      "evidence_to_integrate":[<str>, ...]    // SOURCE urls or search queries from the evidence that the rewriter must use
    }
  }
}

Scoring rubric (each criterion ~1.4 points, total 10):

1. Relevance      — does every section directly address the query?
2. Evidence       — are claims in the report traceable to the provided search results?
                    Quote or paraphrase the relevant search snippet when flagging gaps.
3. Depth          — does it go beyond surface observations into mechanisms and implications?
4. Nuance         — does it distinguish subtleties (short-run vs long-run, etc.)?
5. Completeness   — are all major sub-questions in the query answered?
6. Structure      — clear Introduction, logical body sections, Conclusion?
                    Headings should be specific to the query.
7. Accuracy       — CRITICAL: evaluate ONLY against the search results provided below,
                    NOT against your training knowledge.
                    A claim is accurate if it is supported by the search results.
                    A claim is inaccurate if it contradicts the search results.
                    A claim is UNVERIFIABLE (penalise moderately, not harshly) if the
                    search results are silent on it — do not penalise for topics that
                    simply postdate your training data.

DEPTH-GAP → SEARCH RULE (important):
  If a weak section is flagged for shallow analysis, missing mechanisms, missing
  comparisons, or under-developed implications, the rewriter CANNOT add that
  depth from training knowledge (it is forbidden to do so). In that case you
  MUST:
    - set `needs_more_search` = true, AND
    - put 1–3 concrete, targeted follow-up queries in `search_queries` that
      would surface the missing evidence (name the specific mechanism, metric,
      stakeholder, or comparison).
  Do NOT set needs_more_search=true for merely stylistic issues.

rewrite_instructions guidance:
  - Each entry MUST be a structured object with all three keys above.
  - `missing_angles`: name the specific sub-topics absent from the section.
  - `questions_to_answer`: phrase as answerable questions the rewrite must address.
  - `evidence_to_integrate`: list SOURCE URLs or search queries (verbatim from
     the provided search results) that the rewrite must cite. Leave empty only
     if `needs_more_search` is true and you expect the supplemental search to
     fill this in.
  - If a section only needs prose polish, list one item in `missing_angles`
     describing the polish, and leave the other two arrays empty.

Use EXACT section headings from the report in weak_sections and rewrite_instructions.
"""

# Separator injected between the prompt, search results, and report
EVAL_SEARCH_HEADER = "\n\n=== SEARCH RESULTS (ground truth for Accuracy) ===\n"
EVAL_REPORT_HEADER = "\n\n=== REPORT TO EVALUATE ===\n"