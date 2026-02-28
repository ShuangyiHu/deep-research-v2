"""
celery_app.py
─────────────
Celery application factory.

Start a worker with:
  celery -A deep_research.worker.celery_app worker --loglevel=info
"""

from dotenv import load_dotenv
load_dotenv()  # Celery worker 是独立进程，不继承 uvicorn 的环境，必须手动加载 .env

from celery import Celery
from deep_research.core.config import settings

celery_app = Celery(
    "deep_research",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["deep_research.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    result_expires=86_400,
    task_default_queue="default",
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=1_800,
    task_time_limit=2_000,
)