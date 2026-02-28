"""
config.py — environment variables and pipeline constants.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")
    google_api_key: str = Field(..., alias="GOOGLE_API_KEY")
    sendgrid_api_key: str = Field(..., alias="SENDGRID_API_KEY")

    sendgrid_from_email: str = Field(default="noreply@deepresearch.ai", alias="SENDGRID_FROM_EMAIL")
    sendgrid_from_name: str = Field(default="Deep Research", alias="SENDGRID_FROM_NAME")

    celery_broker_url: str = Field(default="redis://localhost:6379/0", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="redis://localhost:6379/0", alias="CELERY_RESULT_BACKEND")

    pipeline_quality_threshold: int = Field(default=8, alias="PIPELINE_QUALITY_THRESHOLD")
    pipeline_max_iterations: int = Field(default=4, alias="PIPELINE_MAX_ITERATIONS")
    pipeline_max_targeted_searches: int = Field(default=2, alias="PIPELINE_MAX_TARGETED_SEARCHES")
    pipeline_how_many_searches: int = Field(default=5, alias="PIPELINE_HOW_MANY_SEARCHES")
    pipeline_search_query_cap: int = Field(default=5, alias="PIPELINE_SEARCH_QUERY_CAP")
    pipeline_score_gap_threshold: int = Field(default=4, alias="PIPELINE_SCORE_GAP_THRESHOLD")


settings = Settings()


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
    "<exact section heading>": "<specific improvement instructions>"
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

Use EXACT section headings from the report in weak_sections and rewrite_instructions.
"""

# Separator injected between the prompt, search results, and report
EVAL_SEARCH_HEADER = "\n\n=== SEARCH RESULTS (ground truth for Accuracy) ===\n"
EVAL_REPORT_HEADER = "\n\n=== REPORT TO EVALUATE ===\n"