"""Image-processing endpoints — applies `medical-image-std` filters server-side."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from minio.error import S3Error

from app.api.deps import CurrentUser, DBSession, SettingsDep, StorageDep
from app.core.exceptions import NotFoundError
from app.schemas.processing import ApplyFilterRequest, ApplyFilterResponse
from app.services.processing_service import FilterError, ProcessingService
from app.services.study_service import StudyService

router = APIRouter()


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
    study_service = StudyService(db)
    instance = await study_service.get_instance(body.instance_id)
    if instance is None:
        raise NotFoundError("Instance not found.")
    series = await study_service.get_series(instance.series_id)
    if series is None:
        raise NotFoundError("Instance not found.")
    await study_service.get_visible_study(series.study_id, user.id)  # 404 if not visible

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
