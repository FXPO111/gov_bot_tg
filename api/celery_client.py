from __future__ import annotations

from celery import Celery
from celery.result import AsyncResult

from shared.settings import get_settings

settings = get_settings()

celery = Celery(
    "yourbot-api-client",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


def send_task(name: str, *args, **kwargs) -> AsyncResult:
    return celery.send_task(name, args=args, kwargs=kwargs)


def get_result(task_id: str) -> AsyncResult:
    return AsyncResult(task_id, app=celery)
