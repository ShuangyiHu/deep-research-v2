"""
utils.py
────────
Shared utilities used across multiple core modules:
  - with_retry: exponential-backoff retry decorator for API calls
  - safe_extract_json: strips markdown fences and parses JSON defensively
"""

import re
import json
import time
import logging
import functools
from typing import Callable, TypeVar, Any

logger = logging.getLogger(__name__)

# Errors that warrant a retry (all lowercased for substring matching)
RETRYABLE_ERRORS: list[str] = [
    "overloaded",
    "rate",
    "timeout",
    "internal",
    "connection",
    "deadline",
    "service unavailable",
    "529",
    "503",
    "502",
]

F = TypeVar("F", bound=Callable[..., Any])


def with_retry(retries: int = 4, base_wait: float = 2.0) -> Callable[[F], F]:
    """
    Decorator: retry a *synchronous* function on transient API errors.

    Usage:
        @with_retry(retries=4)
        def call_claude(...):
            ...

    Raises the last exception if all retries are exhausted.
    """
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(retries):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    msg = str(exc).lower()
                    if any(token in msg for token in RETRYABLE_ERRORS):
                        wait = base_wait ** attempt
                        logger.warning(
                            "Retryable error on attempt %d/%d ('%s'). "
                            "Waiting %.1fs…",
                            attempt + 1,
                            retries,
                            str(exc)[:120],
                            wait,
                        )
                        time.sleep(wait)
                        last_exc = exc
                    else:
                        raise
            logger.error("All %d retries exhausted.", retries)
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


# JSON fallback returned when parsing fails completely
_JSON_FALLBACK: dict = {
    "score": 3,
    "weak_sections": [
        "Introduction",
        "Task Automation vs Job Elimination",
        "Complementary Skills",
    ],
    "needs_more_search": True,
    "search_queries": [
        "AI impact junior software engineer hiring North America 2025",
        "AI coding tools labor substitution elasticity research",
    ],
    "rewrite_instructions": {
        "Task Automation vs Job Elimination": (
            "Add empirical evidence distinguishing displaced tasks from eliminated roles."
        ),
        "Complementary Skills": (
            "List specific skills with salary data or job posting growth evidence."
        ),
    },
}


def safe_extract_json(text: str) -> dict:
    """
    Parse JSON from a model response, tolerating markdown code fences.

    Strategy:
      1. Strip ```json … ``` fences.
      2. Try json.loads directly.
      3. Try regex-extracting the first {...} block.
      4. Return a safe fallback dict and log a warning.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

    # Attempt 1: direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 2: find first JSON object in the string
    try:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError:
        pass

    logger.warning("JSON parse failed — using safe fallback.\nRaw text:\n%s", text[:500])
    return _JSON_FALLBACK.copy()
