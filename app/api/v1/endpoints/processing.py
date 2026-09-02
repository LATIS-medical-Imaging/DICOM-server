"""Image-processing endpoints — applies `medical-image-std` filters server-side."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DBSession, SettingsDep, StorageDep
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.db.models.instance import Instance
from app.db.models.share import SharePermission
from app.schemas.processing import (
    ApplyFilterRequest,
    ApplyFilterResponse,
    ApplySegmentationRequest,
    ApplySegmentationResponse,
    SegmentationModelInfo,
)
from app.services.derived_pixels import FilterError
from app.services.processing_service import ProcessingService
from app.services.segmentation_service import (
    SegmentationModelError,
    SegmentationService,
)
from app.services.share_service import ShareService
from app.services.study_service import StudyService
from app.services.ws_hub import get_ws_hub

router = APIRouter()


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
    response_model=ApplyFilterResponse,
    status_code=status.HTTP_200_OK,
    summary="Apply a medical-image-std filter to a single DICOM instance",
)
async def apply_filter(
    body: ApplyFilterRequest,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> ApplyFilterResponse:
    # Authorize against the parent study before doing any work — cheaper to
    # bounce here than to load pixels just to find out the user can't see them.
    await _authorize_pixel_write(db, user.id, body.instance_id)

    service = ProcessingService(db, storage, settings)
    try:
        derived_key, cached = await service.apply(
            body.instance_id, body.filter, body.params, body.roi
        )
    except FilterError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Object storage unavailable: {exc}",
        ) from exc

    bucket = settings.minio_bucket_dicom
    expires = settings.minio_presigned_url_expire_seconds
    download_url = storage.presigned_get_url(bucket, derived_key, expires_seconds=expires)

    return ApplyFilterResponse(
        download_url=download_url,
        object_key=derived_key,
        filter=body.filter,
        expires_in=expires,
        cached=cached,
    )


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
    response_model=ApplySegmentationResponse,
    status_code=status.HTTP_200_OK,
    summary="Run a deep-segmentation model over a single DICOM instance",
)
async def apply_segmentation(
    body: ApplySegmentationRequest,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> ApplySegmentationResponse:
    await _authorize_pixel_write(db, user.id, body.instance_id)

    service = SegmentationService(db, storage, settings)
    try:
        derived_key, cached, lesion_count, annotations = await service.apply_segmentation(
            body.instance_id,
            body.model_name,
            body.threshold,
            body.min_lesion_area,
            body.roi,
        )
    except SegmentationModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except FilterError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Object storage unavailable: {exc}",
        ) from exc

    bucket = settings.minio_bucket_dicom
    expires = settings.minio_presigned_url_expire_seconds
    download_url = storage.presigned_get_url(bucket, derived_key, expires_seconds=expires)

    return ApplySegmentationResponse(
        download_url=download_url,
        object_key=derived_key,
        model_name=body.model_name,
        expires_in=expires,
        cached=cached,
        lesion_count=lesion_count,
        annotations=annotations,
    )
