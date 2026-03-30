"""
tasks.py
────────
Celery task definitions.

Tasks here are the bridge between the async pipeline core and Celery's
synchronous worker processes.

Key design decisions:
  - run_pipeline() (sync wrapper) is called directly — it uses asyncio.run()
    internally, which is safe inside Celery's forked worker processes.
  - Progress messages are stored in the Celery result backend as a list,
    accessible by polling the task's `info` field while status == "PROGRESS".
  - Email delivery (optional) happens at the end of the same task so there's
    no need for a chained task in Phase 1.

Usage from application code:
    from deep_research.worker.tasks import run_research_task

    job = run_research_task.delay(query="...", email="user@example.com")
    job.id   # → task_id to poll
"""

import logging
from typing import Optional

from celery import Task

from deep_research.worker.celery_app import celery_app
from deep_research.core.pipeline import run_pipeline
from deep_research.services.email_service import send_report

logger = logging.getLogger(__name__)


class ResearchTask(Task):
    """Custom base Task class — adds typed `on_progress` wiring."""
    abstract = True


@celery_app.task(
    bind=True,
    base=ResearchTask,
    name="deep_research.run_research",
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,          # Acknowledge only after task completes (safer)
    reject_on_worker_lost=True,
)
def run_research_task(
    self: Task,
    query: str,
    email: Optional[str] = None,
    threshold: Optional[int] = None,
    max_iter: Optional[int] = None,
) -> dict:
    """
Celery task: run the full 8-agent Deep Research pipeline for a user query.
 
Agent pipeline executed inside this task:
    1. QueryRewriterAgent  — expand vague queries before search
    2. PlannerAgent        — generate N typed search queries
    3. SearchAgent ×N      — parallel web search, returns SearchDocumentCollection
    4. AnalystAgent        — deduplicate, score, filter evidence (no LLM)
    5. WriterAgent         — draft report from structured evidence
    [iterative loop up to max_iter:]
    6. EvaluatorAgent      — Claude + Gemini consensus score + weak section detection
    7. SearchAgent ×N      — targeted search if needs_more_search (optional)
    8. AnalystAgent        — re-score new evidence before merging (optional)
    9. RewriteAgent        — rewrite weak sections with new evidence
   10. StructureAgent      — fix transitions, remove duplicates
 
Progress updates are pushed to the Celery result backend as:
    {"state": "PROGRESS", "meta": {"log": ["Planning searches…", ...]}}
 
On completion:
    {"report": "<markdown>", "email_sent": True|False|None, "log": [...]}
 
Args:
    query:     The user's research question (raw — QueryRewriterAgent handles expansion).
    email:     Optional address for SendGrid delivery on completion.
    threshold: Quality score (1–10) at which iteration stops. Defaults to settings.
    max_iter:  Max refinement iterations. Defaults to settings.
"""
    progress_log: list[str] = []

    def on_progress(msg: str) -> None:
        """Append to log and push a PROGRESS update to the backend."""
        progress_log.append(msg)
        self.update_state(
            state="PROGRESS",
            meta={"log": progress_log},
        )
        logger.info("[task %s] %s", self.request.id, msg)

    try:
        on_progress(f"Starting research: {query[:120]}")

        report = run_pipeline(
            query=query,
            threshold=threshold,
            max_iter=max_iter,
            on_progress=on_progress,
        )

        # Optional email delivery
        email_sent: Optional[bool] = None
        if email:
            on_progress(f"Sending report to {email}…")
            email_sent = send_report(
                to_email=email,
                query=query,
                markdown_report=report,
            )
            status = "✓ Email sent" if email_sent else "✗ Email failed (check logs)"
            on_progress(status)

        on_progress("✓ Task complete.")
        return {
            "report": report,
            "email_sent": email_sent,
            "log": progress_log,
        }

    except Exception as exc:
        logger.exception("Task %s failed: %s", self.request.id, exc)
        # Celery will retry up to max_retries times for unexpected exceptions
        raise self.retry(exc=exc)
