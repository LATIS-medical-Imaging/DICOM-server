"""Image-processing endpoints — filters and deep segmentation.

Both apply endpoints are *cache-first, queue-second*: an already-computed
result comes back immediately with 200, and anything that has to be computed is
handed to a Celery worker and answered with 202 plus a job id. Clients follow
the job over the WebSocket (`filter.*` / `segmentation.*` events) or by polling
`GET /processing/jobs/{job_id}`.

Computing inline used to hold the request open for the whole run, which blocked
the API's event loop and put long jobs at the mercy of proxy request timeouts.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DBSession, SettingsDep, StorageDep
from app.core.config import Settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.db.models.instance import Instance
from app.db.models.share import SharePermission
from app.schemas.processing import (
    ApplyFilterRequest,
    ApplyFilterResponse,
    ApplySegmentationRequest,
    ApplySegmentationResponse,
    PixelJobStatus,
    SegmentationModelInfo,
)
from app.services import job_store
from app.services.derived_pixels import FilterError
from app.services.processing_service import ProcessingService
from app.services.segmentation_service import (
    SegmentationService,
)
from app.services.share_service import ShareService
from app.services.storage_service import StorageService
from app.services.study_service import StudyService
from app.services.ws_hub import get_ws_hub

router = APIRouter()


def _presign(storage: StorageService, settings: Settings, key: str) -> str:
    return storage.presigned_get_url(
        settings.minio_bucket_dicom,
        key,
        expires_seconds=settings.minio_presigned_url_expire_seconds,
    )


async def _authorize_pixel_write(
    db: AsyncSession, user_id: uuid.UUID, instance_id: uuid.UUID
) -> Instance:
    """Owner, or an active share with ANNOTATE/MANAGE on the instance's series.

    Both pixel-writing endpoints use this — a segmentation mask is a derived
    object exactly like a filter result, so it needs the same permission.
    """
    study_service = StudyService(db)
    instance = await study_service.get_instance(instance_id)
    if instance is None:
        raise NotFoundError("Instance not found.")
    series = await study_service.get_series(instance.series_id)
    if series is None:
        raise NotFoundError("Instance not found.")
    study = await study_service.get_visible_study(series.study_id, user_id)

    # VIEW-only grantees cannot mutate pixels (no filter, no annotation save).
    if study.owner_id != user_id:
        share_service = ShareService(db, get_ws_hub())
        perm = await share_service.caller_permission_for_series(user_id, series)
        if perm not in (SharePermission.ANNOTATE, SharePermission.MANAGE):
            raise PermissionDeniedError(
                "You don't have permission to write derived pixels on this series."
            )
    return instance


@router.post(
    "/apply",
    response_model=None,
    summary="Apply a medical-image-std filter to a single DICOM instance",
    responses={
        200: {"model": ApplyFilterResponse, "description": "Cached — result ready"},
        202: {"model": PixelJobStatus, "description": "Queued — follow the job"},
    },
)
async def apply_filter(
    body: ApplyFilterRequest,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,
    response: Response,
) -> ApplyFilterResponse | PixelJobStatus:
    # Authorize against the parent study before doing any work — cheaper to
    # bounce here than to load pixels just to find out the user can't see them.
    # This also hands us the instance, so the cache probe needs no second query.
    instance = await _authorize_pixel_write(db, user.id, body.instance_id)

    service = ProcessingService(db, storage, settings)
    try:
        cached_key = await asyncio.to_thread(
            service.cached_key, instance.file_path, body.filter, body.params, body.roi
        )
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Object storage unavailable: {exc}",
        ) from exc

    if cached_key is not None:
        return ApplyFilterResponse(
            download_url=_presign(storage, settings, cached_key),
            object_key=cached_key,
            filter=body.filter,
            expires_in=settings.minio_presigned_url_expire_seconds,
            cached=True,
        )

    # Lazy import to avoid circular dependency at module load time, matching
    # UploadService's enqueue path.
    from app.workers.tasks.processing import apply_filter_task

    job_id = job_store.new_job_id()
    await job_store.create(job_id, user.id, "filter", filter=body.filter)
    apply_filter_task.delay(
        job_id=job_id,
        owner_id=str(user.id),
        instance_id=str(body.instance_id),
        filter_name=body.filter,
        params=body.params,
        roi=body.roi.model_dump() if body.roi else None,
    )
    response.status_code = status.HTTP_202_ACCEPTED
    return PixelJobStatus(job_id=job_id, kind="filter", status="queued", filter=body.filter)


@router.get(
    "/segmentation/models",
    response_model=list[SegmentationModelInfo],
    summary="List the deep-segmentation checkpoints the model server offers",
)
async def list_segmentation_models(
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> list[SegmentationModelInfo]:
    service = SegmentationService(db, storage, settings)
    try:
        models = await service.list_models()
    except FilterError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return [SegmentationModelInfo(**m) for m in models]


@router.post(
    "/segmentation/apply",
    response_model=None,
    summary="Run a deep-segmentation model over a single DICOM instance",
    responses={
        200: {"model": ApplySegmentationResponse, "description": "Cached — result ready"},
        202: {"model": PixelJobStatus, "description": "Queued — follow the job"},
    },
)
async def apply_segmentation(
    body: ApplySegmentationRequest,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,
    response: Response,
) -> ApplySegmentationResponse | PixelJobStatus:
    instance = await _authorize_pixel_write(db, user.id, body.instance_id)

    service = SegmentationService(db, storage, settings)
    try:
        cached = await asyncio.to_thread(
            service.cached_result,
            instance.file_path,
            body.model_name,
            body.threshold,
            body.min_lesion_area,
            body.roi,
        )
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Object storage unavailable: {exc}",
        ) from exc

    if cached is not None:
        derived_key, lesion_count, annotations, has_png = cached
        return ApplySegmentationResponse(
            download_url=_presign(storage, settings, derived_key),
            object_key=derived_key,
            model_name=body.model_name,
            expires_in=settings.minio_presigned_url_expire_seconds,
            cached=True,
            lesion_count=lesion_count,
            annotations=annotations,
            mask_png_url=_presign(storage, settings, f"{derived_key}.png") if has_png else None,
        )

    from app.workers.tasks.processing import apply_segmentation_task

    job_id = job_store.new_job_id()
    await job_store.create(job_id, user.id, "segmentation", model_name=body.model_name)
    apply_segmentation_task.delay(
        job_id=job_id,
        owner_id=str(user.id),
        instance_id=str(body.instance_id),
        model_name=body.model_name,
        threshold=body.threshold,
        min_lesion_area=body.min_lesion_area,
        roi=body.roi.model_dump() if body.roi else None,
    )
    response.status_code = status.HTTP_202_ACCEPTED
    return PixelJobStatus(
        job_id=job_id, kind="segmentation", status="queued", model_name=body.model_name
    )


@router.get(
    "/jobs/{job_id}",
    response_model=PixelJobStatus,
    summary="Poll a queued filter/segmentation job (must belong to the caller)",
)
async def get_pixel_job(
    job_id: str,
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> PixelJobStatus:
    record = await job_store.read(job_id)
    # A job whose record expired and one that never existed are the same 404 —
    # the caller's move is identical either way: re-issue the apply request,
    # which will hit the cache if the work did finish.
    if record is None or record.get("owner_id") != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or already expired."
        )

    payload: dict[str, Any] = {k: v for k, v in record.items() if k in PixelJobStatus.model_fields}
    object_key = record.get("object_key")
    if record.get("status") == job_store.JobStatus.COMPLETED and object_key:
        # Presigned URLs are minted on read, never stored — a stored one would
        # expire while the record is still live.
        payload["download_url"] = _presign(storage, settings, object_key)
        payload["expires_in"] = settings.minio_presigned_url_expire_seconds
        if record.get("kind") == "segmentation" and record.get("has_png"):
            payload["mask_png_url"] = _presign(storage, settings, f"{object_key}.png")

    return PixelJobStatus(**payload)
