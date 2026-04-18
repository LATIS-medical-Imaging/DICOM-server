"""Celery application factory.

Tasks are discovered automatically from ``app.workers.tasks``.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "dicom-server",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_default_queue=settings.celery_task_default_queue,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)


@celery_app.task(name="workers.ping")
def ping() -> str:
    """Trivial smoke-test task used to verify the worker is wired up."""
    return "pong"
