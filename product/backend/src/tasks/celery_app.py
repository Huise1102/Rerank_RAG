"""Celery 实例。

broker 与结果后端都使用 Redis，任务模块在实例创建时注册，worker 需要单独启动。
"""

from __future__ import annotations

from celery import Celery

from src.core.config import settings

celery_app = Celery(
    "rag",
    broker=settings.redis.url,
    backend=settings.redis.url,
    include=["src.tasks.document_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_track_started=True,
    task_acks_late=False,
    worker_max_tasks_per_child=20,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
)
