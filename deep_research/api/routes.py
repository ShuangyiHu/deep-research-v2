"""
routes.py
─────────
FastAPI router — all HTTP endpoints live here.

Endpoints:
  POST /generate            → queue a research task, return job_id
  GET  /status/{job_id}     → poll task state + streaming progress log
  DELETE /cancel/{job_id}   → revoke a queued or running task

The router is mounted by main.py with a /api/v1 prefix, so the full paths are:
  POST   /api/v1/generate
  GET    /api/v1/status/{job_id}
  DELETE /api/v1/cancel/{job_id}
"""

import logging
from fastapi import APIRouter, HTTPException, status
from celery.result import AsyncResult

from deep_research.worker.tasks import run_research_task
from deep_research.api.schemas import GenerateRequest, GenerateResponse, JobStatus

logger = logging.getLogger(__name__)

router = APIRouter()


# ── POST /generate ─────────────────────────────────────────────────────────────

@router.post(
    "/generate",
    response_model=GenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a research task",
    description=(
        "Accepts a research query and optional email address. "
        "Queues the pipeline as a Celery background task and immediately "
        "returns a `job_id`. Poll `/status/{job_id}` to track progress."
    ),
)
async def generate(body: GenerateRequest) -> GenerateResponse:
    """
    Queue the Deep Research pipeline for a user query.

    Returns 202 Accepted with a job_id — the client should then poll
    GET /status/{job_id} until state == SUCCESS or FAILURE.
    """
    logger.info("Queuing research task | query_len=%d email=%s", len(body.query), body.email)

    task = run_research_task.delay(
        query=body.query,
        email=str(body.email) if body.email else None,
        threshold=body.threshold,
        max_iter=body.max_iter,
    )

    logger.info("Task queued | job_id=%s", task.id)
    return GenerateResponse(job_id=task.id)


# ── GET /status/{job_id} ───────────────────────────────────────────────────────

@router.get(
    "/status/{job_id}",
    response_model=JobStatus,
    summary="Poll task status",
    description=(
        "Returns the current state of a research task. "
        "While running, `log` streams progress messages. "
        "On completion, `report` contains the full markdown report."
    ),
)
async def get_status(job_id: str) -> JobStatus:
    """
    Map Celery task states to our JobStatus schema.

    Celery states and what we expose:
      PENDING  → job is queued or job_id is unknown
      STARTED  → worker picked it up (normalised to PROGRESS)
      PROGRESS → custom state pushed by the task via update_state()
      SUCCESS  → task returned successfully
      FAILURE  → task raised an exception
      REVOKED  → task was cancelled via DELETE /cancel/{job_id}
    """
    result = AsyncResult(job_id)
    state = result.state

    # ── PENDING ────────────────────────────────────────────────────────────────
    if state == "PENDING":
        return JobStatus(job_id=job_id, state="PENDING")

    # ── PROGRESS (or STARTED, normalised) ─────────────────────────────────────
    if state in ("STARTED", "PROGRESS"):
        meta = result.info or {}
        return JobStatus(
            job_id=job_id,
            state="PROGRESS",
            log=meta.get("log", []),
        )

    # ── SUCCESS ────────────────────────────────────────────────────────────────
    if state == "SUCCESS":
        task_result: dict = result.result or {}
        return JobStatus(
            job_id=job_id,
            state="SUCCESS",
            log=task_result.get("log", []),
            report=task_result.get("report"),
            email_sent=task_result.get("email_sent"),
        )

    # ── FAILURE ────────────────────────────────────────────────────────────────
    if state == "FAILURE":
        exc = result.result  # Celery stores the exception here on failure
        error_msg = str(exc) if exc else "Unknown error"
        logger.error("Task %s failed: %s", job_id, error_msg)
        return JobStatus(
            job_id=job_id,
            state="FAILURE",
            error=error_msg,
        )

    # ── REVOKED ────────────────────────────────────────────────────────────────
    if state == "REVOKED":
        return JobStatus(job_id=job_id, state="REVOKED")

    # ── Unknown state (shouldn't happen, but be defensive) ────────────────────
    logger.warning("Unexpected Celery state '%s' for job %s", state, job_id)
    return JobStatus(job_id=job_id, state=state)


# ── DELETE /cancel/{job_id} ───────────────────────────────────────────────────

@router.delete(
    "/cancel/{job_id}",
    status_code=status.HTTP_200_OK,
    summary="Cancel a queued or running task",
    description=(
        "Sends a revoke signal to Celery. "
        "PENDING tasks are cancelled immediately. "
        "PROGRESS tasks receive a termination signal — "
        "they may take a moment to stop."
    ),
)
async def cancel(job_id: str) -> dict:
    """
    Revoke a Celery task by ID.

    `terminate=True` sends SIGTERM to a running worker process.
    `signal='SIGKILL'` is avoided — SIGTERM lets the worker clean up.
    """
    result = AsyncResult(job_id)
    current_state = result.state

    if current_state == "SUCCESS":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {job_id} has already completed successfully.",
        )
    if current_state == "REVOKED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {job_id} is already revoked.",
        )

    result.revoke(terminate=True)
    logger.info("Task %s revoked (was: %s)", job_id, current_state)

    return {"job_id": job_id, "cancelled": True, "previous_state": current_state}
