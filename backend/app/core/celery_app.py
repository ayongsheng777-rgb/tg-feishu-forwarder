"""Celery application for async media download / upload tasks."""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "tg_feishu",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.forward_tasks", "app.tasks.media_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_max_retries=3,
    task_retry_delay=30,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
