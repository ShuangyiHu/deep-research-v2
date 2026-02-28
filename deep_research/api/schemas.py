"""
schemas.py
──────────
Pydantic models for all API request and response bodies.

Keeping them in a separate file means routes.py stays readable,
and the Gradio UI can import these models to validate inputs
without importing FastAPI itself.
"""

from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Request bodies ─────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=20,
        max_length=2000,
        description="The research question to investigate.",
        examples=[
            "How are AI coding tools expected to shift hiring demand "
            "for junior software engineers in North America between 2025 and 2030?"
        ],
    )
    email: Optional[EmailStr] = Field(
        default=None,
        description="If provided, the finished report is emailed here via SendGrid.",
    )
    threshold: Optional[int] = Field(
        default=None,
        ge=1,
        le=10,
        description="Quality score (1-10) at which iteration stops. Defaults to server setting.",
    )
    max_iter: Optional[int] = Field(
        default=None,
        ge=1,
        le=8,
        description="Maximum refinement iterations. Defaults to server setting.",
    )

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must not be blank")
        return v.strip()


# ── Response bodies ────────────────────────────────────────────────────────────

class GenerateResponse(BaseModel):
    job_id: str = Field(description="Celery task ID — use this to poll /status/{job_id}.")
    message: str = Field(default="Research task queued.")


class JobStatus(BaseModel):
    """
    Returned by GET /status/{job_id}.

    State machine:
        PENDING   → task is queued, not yet picked up by a worker
        PROGRESS  → worker is running, `log` contains progress messages
        SUCCESS   → pipeline finished; `report` contains the markdown
        FAILURE   → task raised an unrecoverable exception; `error` has details
        REVOKED   → task was cancelled
    """
    job_id: str
    state: str  # PENDING | PROGRESS | SUCCESS | FAILURE | REVOKED

    # Populated during PROGRESS
    log: list[str] = Field(default_factory=list)

    # Populated on SUCCESS
    report: Optional[str] = Field(default=None)
    email_sent: Optional[bool] = Field(default=None)

    # Populated on FAILURE
    error: Optional[str] = Field(default=None)
