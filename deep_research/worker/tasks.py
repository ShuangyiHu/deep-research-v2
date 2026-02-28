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
    Celery task: run the full Deep Research pipeline for a user query.

    Progress updates are pushed to the result backend as:
        {
            "state": "PROGRESS",
            "meta": {
                "log": ["Planning searches…", "→ 5 searches planned", …]
            }
        }

    On completion:
        {
            "state": "SUCCESS",
            "result": {
                "report": "<markdown string>",
                "email_sent": True | False | None
            }
        }

    Args:
        query:     The user's research question.
        email:     Optional email address to send the finished report to.
        threshold: Quality threshold (overrides settings default).
        max_iter:  Max refinement iterations (overrides settings default).
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
