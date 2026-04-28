"""DICOM file upload and download endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from minio.error import S3Error

from app.api.deps import SettingsDep, StorageDep
from app.schemas.dicom import (
    PresignedDownloadResponse,
    PresignedUploadRequest,
    PresignedUploadResponse,
)

router = APIRouter()


@router.post(
    "/presign-upload",
    response_model=PresignedUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Request a presigned PUT URL for direct-to-MinIO upload",
)
def presign_upload(
    body: PresignedUploadRequest,
    storage: StorageDep,
    settings: SettingsDep,
) -> PresignedUploadResponse:
    """Generate a short-lived presigned URL the client uses to PUT a .dcm file
    directly into MinIO — no DICOM bytes pass through the API server.
    """
    key = storage.dicom_object_key(
        owner_id=body.owner_id,
        study_uid=body.study_instance_uid,
        series_uid=body.series_instance_uid,
        sop_uid=body.sop_instance_uid,
    )
    bucket = settings.minio_bucket_dicom
    expires = settings.minio_presigned_url_expire_seconds

    try:
        upload_url = storage.presigned_put_url(bucket, key, expires_seconds=expires)
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Object storage unavailable: {exc}",
        ) from exc

    return PresignedUploadResponse(
        upload_url=upload_url,
        object_key=key,
        bucket=bucket,
        expires_in=expires,
    )


@router.get(
    "/presign-download",
    response_model=PresignedDownloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Request a presigned GET URL for direct-from-MinIO download",
)
def presign_download(
    storage: StorageDep,
    settings: SettingsDep,
    object_key: str = Query(..., description="MinIO object key returned by presign-upload."),
) -> PresignedDownloadResponse:
    """Generate a short-lived presigned URL CornerstoneJS uses to stream a .dcm
    file directly from MinIO — no DICOM bytes pass through the API server.
    """
    bucket = settings.minio_bucket_dicom
    expires = settings.minio_presigned_url_expire_seconds

    if not storage.object_exists(bucket, object_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Object '{object_key}' not found in bucket '{bucket}'.",
        )

    try:
        download_url = storage.presigned_get_url(bucket, object_key, expires_seconds=expires)
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Object storage unavailable: {exc}",
        ) from exc

    return PresignedDownloadResponse(
        download_url=download_url,
        object_key=object_key,
        expires_in=expires,
    )
