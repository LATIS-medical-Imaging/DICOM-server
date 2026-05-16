"""Image-processing endpoints — applies `medical-image-std` filters server-side."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from minio.error import S3Error

from app.api.deps import DBSession, SettingsDep, StorageDep
from app.schemas.processing import ApplyFilterRequest, ApplyFilterResponse
from app.services.processing_service import FilterError, ProcessingService

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
) -> ApplyFilterResponse:
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
