"""Celery application configuration."""
from celery import Celery

from app.config import settings

celery_app = Celery(
    "ocr_platform",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.ocr_tasks",
        "app.tasks.gpt_tasks",
        "app.tasks.kg_tasks",
        "app.tasks.rag_tasks",
        "app.tasks.knowledge_tasks",
        "app.tasks.insight_tasks",
    ],

)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,
    worker_concurrency=settings.celery_worker_concurrency,
    worker_pool=settings.celery_worker_pool,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)
