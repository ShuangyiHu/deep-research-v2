"""
a2a/server.py
─────────────
Mounts all A2A agent servers on the FastAPI application.

Usage (called from main.py):
    from deep_research.a2a.server import register_a2a_apps
    register_a2a_apps(app)

Each agent is mounted as a sub-application at /a2a/<name>:
    /a2a/analyst/.well-known/agent-card.json   ← A2A discovery
    /a2a/analyst/                               ← JSON-RPC endpoint

Agents are added progressively in Phase C–F. Until then this is a no-op
that can be imported safely.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def register_a2a_apps(app: FastAPI) -> None:
    """
    Mount all A2A agent servers as sub-applications on an existing FastAPI app.
    Populated incrementally in Phase C (analyst) → D (search) → E (writer) → F (evaluator).

    Each A2A app is built via its build_*_app().build() and then mounted at /a2a/<name>
    using FastAPI's sub-application mount (ASGI mount), which correctly scopes all routes.
    """
    # Phase C: Analyst
    try:
        from deep_research.a2a.executors.analyst import build_analyst_app
        analyst_sub = build_analyst_app().build()
        app.mount("/a2a/analyst", analyst_sub)
        logger.info("[A2A] Analyst agent mounted at /a2a/analyst")
    except ImportError:
        pass  # executor not yet implemented

    # Phase D: Search
    try:
        from deep_research.a2a.executors.search import build_search_app
        search_sub = build_search_app().build()
        app.mount("/a2a/search", search_sub)
        logger.info("[A2A] Search agent mounted at /a2a/search")
    except ImportError:
        pass

    # Phase E: Writer
    try:
        from deep_research.a2a.executors.writer import build_writer_app
        writer_sub = build_writer_app().build()
        app.mount("/a2a/writer", writer_sub)
        logger.info("[A2A] Writer agent mounted at /a2a/writer")
    except ImportError:
        pass

    # Phase F: Evaluator
    try:
        from deep_research.a2a.executors.evaluator import build_evaluator_app
        evaluator_sub = build_evaluator_app().build()
        app.mount("/a2a/evaluator", evaluator_sub)
        logger.info("[A2A] Evaluator agent mounted at /a2a/evaluator")
    except ImportError:
        pass
