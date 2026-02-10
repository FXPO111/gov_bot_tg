from __future__ import annotations

from celery import Celery

from shared.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "yourbot-worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_max_tasks_per_child=200,
)
