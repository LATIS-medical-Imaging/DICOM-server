"""DICOM ingestion Celery task — orchestrates status updates and delegates to IngestionService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.upload_job import UploadJob, UploadJobStatus
from app.db.session import get_sync_db
from app.services.ingestion_service import IngestionService
from app.services.storage_service import StorageService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="workers.ingest_dicom_instance",
    max_retries=3,
    default_retry_delay=60,
)
def ingest_dicom_instance(
    self: Any,
    job_id: str,
    object_key: str,
    owner_id: str,
    file_size_bytes: int,
) -> dict[str, str]:
    settings = get_settings()
    storage = StorageService(settings)

    _set_job_status(job_id, UploadJobStatus.PROCESSING, started_at=datetime.now(UTC))
    logger.info("ingest_started", job_id=job_id, object_key=object_key)

    try:
        _set_job_status(job_id, UploadJobStatus.EXTRACTING_METADATA)

        with get_sync_db() as db:
            service = IngestionService(db, storage, settings)
            _set_job_status(job_id, UploadJobStatus.STORING)
            study_id = service.ingest(object_key, owner_id, file_size_bytes)
            db.execute(
                update(UploadJob).where(UploadJob.id == uuid.UUID(job_id)).values(study_id=study_id)
            )

        _set_job_status(
            job_id,
            UploadJobStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            processed_files=1,
            processed_bytes=file_size_bytes,
        )
        logger.info("ingest_completed", job_id=job_id, object_key=object_key)
        return {"job_id": job_id, "status": "completed"}

    except Exception as exc:
        logger.error("ingest_failed", job_id=job_id, error=str(exc), exc_info=True)
        _set_job_status(
            job_id,
            UploadJobStatus.FAILED,
            failed_files=1,
            errors=[{"error": str(exc), "object_key": object_key}],
        )
        raise self.retry(exc=exc) from exc


def _set_job_status(
    job_id: str,
    status: str,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    processed_files: int | None = None,
    processed_bytes: int | None = None,
    failed_files: int | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> None:
    # Each status update is its own transaction so the polling endpoint sees
    # progress in real time rather than waiting for the full task to commit.
    values: dict[str, Any] = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if processed_files is not None:
        values["processed_files"] = processed_files
    if processed_bytes is not None:
        values["processed_bytes"] = processed_bytes
    if failed_files is not None:
        values["failed_files"] = failed_files
    if errors is not None:
        values["errors"] = errors

    with get_sync_db() as db:
        db.execute(update(UploadJob).where(UploadJob.id == uuid.UUID(job_id)).values(**values))
