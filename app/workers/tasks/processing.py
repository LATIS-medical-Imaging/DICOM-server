"""Celery tasks for derived-pixel work — filters and deep segmentation.

Both endpoints used to compute inline, holding the HTTP request open for the
whole run. That blocked the API's event loop, gave the viewer no progress to
show, and put a long job at the mercy of any proxy's request timeout (100 s at
Cloudflare, surfacing as a 524).

The task owns job status and error handling and delegates the work to the
service, matching `ingest_dicom_instance`. Progress and the final outcome go to
the browser over the existing Redis → WebSocketHub bridge, and are also readable
via `GET /processing/jobs/{job_id}` for clients that would rather poll.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.instance import Instance
from app.db.session import get_sync_db
from app.schemas.processing import ROI
from app.services.derived_pixels import FilterError
from app.services.job_store import JobStatus, update_sync
from app.services.processing_service import ProcessingService
from app.services.redis_publisher import publish_ws_event
from app.services.segmentation_service import SegmentationModelError, SegmentationService
from app.services.storage_service import StorageService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(bind=True, name="workers.apply_filter")
def apply_filter_task(
    self: Any,
    job_id: str,
    owner_id: str,
    instance_id: str,
    filter_name: str,
    params: dict[str, Any],
    roi: dict[str, int] | None = None,
) -> dict[str, Any]:
    def work(storage: StorageService, source_key: str, on_stage: Any) -> dict[str, Any]:
        service = ProcessingService(None, storage, get_settings())
        derived_key, cached = service.apply_to_key(
            source_key, filter_name, params, ROI(**roi) if roi else None, on_stage
        )
        return {"object_key": derived_key, "cached": cached, "filter": filter_name}

    return _run_job(job_id, owner_id, instance_id, "filter", work)


@celery_app.task(bind=True, name="workers.apply_segmentation")
def apply_segmentation_task(
    self: Any,
    job_id: str,
    owner_id: str,
    instance_id: str,
    model_name: str,
    threshold: float | None = None,
    min_lesion_area: int | None = None,
    roi: dict[str, int] | None = None,
) -> dict[str, Any]:
    def work(storage: StorageService, source_key: str, on_stage: Any) -> dict[str, Any]:
        service = SegmentationService(None, storage, get_settings())
        derived_key, cached, lesion_count, annotations, has_png = service.apply_to_key(
            source_key,
            model_name,
            threshold,
            min_lesion_area,
            ROI(**roi) if roi else None,
            on_stage,
        )
        return {
            "object_key": derived_key,
            "cached": cached,
            "model_name": model_name,
            "lesion_count": lesion_count,
            "annotations": annotations,
            "has_png": has_png,
        }

    return _run_job(job_id, owner_id, instance_id, "segmentation", work)


def _run_job(
    job_id: str,
    owner_id: str,
    instance_id: str,
    kind: str,
    work: Any,
) -> dict[str, Any]:
    """Shared status/error handling for both pixel tasks.

    The services take a source object key rather than an instance id, so the
    only DB access here is resolving that key.
    """
    settings = get_settings()
    storage = StorageService(settings)

    def on_stage(stage: str) -> None:
        update_sync(job_id, status=JobStatus.RUNNING, stage=stage)
        publish_ws_event(owner_id, f"{kind}.progress", {"job_id": job_id, "stage": stage})

    update_sync(job_id, status=JobStatus.RUNNING, stage="starting")

    try:
        with get_sync_db() as db:
            row = db.execute(
                select(Instance.file_path).where(Instance.id == uuid.UUID(instance_id))
            ).scalar_one_or_none()
        if row is None:
            raise FilterError(f"Instance {instance_id} not found.")

        result: dict[str, Any] = work(storage, row, on_stage)
    except (FilterError, SegmentationModelError) as exc:
        # Domain failures: a bad request or an unreachable model server. Both
        # are final — retrying would fail identically — so they are reported,
        # not raised, and the endpoint maps them to 400/503 on the poll.
        return _fail(job_id, owner_id, kind, exc)
    except Exception as exc:
        logger.exception("pixel_job_failed", job_id=job_id, kind=kind)
        return _fail(job_id, owner_id, kind, exc)

    update_sync(job_id, status=JobStatus.COMPLETED, stage="done", error=None, **result)
    publish_ws_event(owner_id, f"{kind}.completed", {"job_id": job_id, **result})
    logger.info("pixel_job_completed", job_id=job_id, kind=kind, cached=result.get("cached"))
    return result


def _fail(job_id: str, owner_id: str, kind: str, exc: Exception) -> dict[str, Any]:
    error = str(exc)
    error_kind = type(exc).__name__
    update_sync(job_id, status=JobStatus.FAILED, stage=None, error=error, error_kind=error_kind)
    publish_ws_event(owner_id, f"{kind}.failed", {"job_id": job_id, "error": error})
    logger.warning("pixel_job_failed", job_id=job_id, kind=kind, error=error)
    return {"error": error, "error_kind": error_kind}
