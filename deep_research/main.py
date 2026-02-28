"""
main.py
───────
FastAPI application factory and entry point.

Run with:
  uvicorn deep_research.main:app --reload --port 8000

Or in production:
  uvicorn deep_research.main:app --workers 4 --port 8000
"""

import logging
import logging.config

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import gradio as gr

from deep_research.api.routes import router
from deep_research.core.config import settings

# ── Logging ───────────────────────────────────────────────────────────────────
logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            }
        },
        "root": {"level": "INFO", "handlers": ["console"]},
        # Silence noisy third-party loggers
        "loggers": {
            "httpx": {"level": "WARNING"},
            "openai": {"level": "WARNING"},
            "anthropic": {"level": "WARNING"},
        },
    }
)

logger = logging.getLogger(__name__)


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Deep Research API",
        description=(
            "Multi-model AI research pipeline. "
            "Submit a query, get a polished research report. "
            "Optionally receive it by email."
        ),
        version="2.0.0",
        docs_url="/docs",       # Swagger UI
        redoc_url="/redoc",     # ReDoc UI
    )

    # ── CORS ─────────────────────────────────────────────────────────────────
    # In production, replace "*" with your actual frontend origin(s).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(router, prefix="/api/v1", tags=["Research"])

    # ── Gradio UI (mounted at /) ──────────────────────────────────────────────
    # Importing here avoids circular imports and keeps startup fast when
    # running the API without the UI (e.g. in a pure-worker deploy).
    from deep_research.ui.app import create_gradio_app
    gradio_app = create_gradio_app()
    app = gr.mount_gradio_app(app, gradio_app, path="/")

    # ── Health check (no auth, used by load balancers / k8s probes) ──────────
    @app.get("/health", tags=["Meta"], include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    # ── Startup / shutdown hooks ──────────────────────────────────────────────
    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info("Deep Research API starting up.")
        logger.info(
            "Pipeline defaults — threshold: %d  max_iter: %d",
            settings.pipeline_quality_threshold,
            settings.pipeline_max_iterations,
        )

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        logger.info("Deep Research API shutting down.")

    return app


app = create_app()
