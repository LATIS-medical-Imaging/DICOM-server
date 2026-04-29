"""Celery task modules.

Add new task modules here and they will be auto-discovered by
``app.workers.celery_app`` via its ``include`` list.
"""

from app.workers.tasks import ingest as ingest
