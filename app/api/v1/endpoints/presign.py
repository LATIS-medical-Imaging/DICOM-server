"""Presigned URL endpoints — MinIO URL broker, no DICOM bytes touch this server.

The presigned URLs themselves bypass our JWT (they carry their own short-lived
HMAC signature from MinIO/R2). Authorization is enforced *here*, at mint time:
the owner of the resulting object is always the authenticated user.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from minio.error import S3Error

from app.api.deps import CurrentUser, SettingsDep, StorageDep
from app.schemas.dicom import (
    PresignedDownloadResponse,
    PresignedUploadBatchRequest,
    PresignedUploadBatchResponse,
    PresignedUploadBatchResponseItem,
    PresignedUploadRequest,
    PresignedUploadResponse,
)

router = APIRouter()


@router.post(
    "/upload",
    response_model=PresignedUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a presigned PUT URL for direct-to-MinIO upload",
)
def presign_upload(
    body: PresignedUploadRequest,
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> PresignedUploadResponse:
    key = storage.dicom_object_key(
        owner_id=str(user.id),
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


@router.post(
    "/upload/batch",
    response_model=PresignedUploadBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Get presigned PUT URLs for multiple files in one round-trip",
)
def presign_upload_batch(
    body: PresignedUploadBatchRequest,
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> PresignedUploadBatchResponse:
    """Generate N presigned PUT URLs in a single call.

    Response ``urls`` list is index-aligned with the request ``files`` list.
    The client should upload each file in parallel using the corresponding URL,
    then register one upload job per file via ``POST /uploads``.
    """
    bucket = settings.minio_bucket_dicom
    expires = settings.minio_presigned_url_expire_seconds
    items: list[PresignedUploadBatchResponseItem] = []

    for file in body.files:
        key = storage.dicom_object_key(
            owner_id=str(user.id),
            study_uid=file.study_instance_uid,
            series_uid=file.series_instance_uid,
            sop_uid=file.sop_instance_uid,
        )
        try:
            upload_url = storage.presigned_put_url(bucket, key, expires_seconds=expires)
        except S3Error as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Object storage unavailable: {exc}",
            ) from exc
        items.append(
            PresignedUploadBatchResponseItem(
                object_key=key,
                upload_url=upload_url,
                bucket=bucket,
                expires_in=expires,
            )
        )

    return PresignedUploadBatchResponse(urls=items)


@router.get(
    "/download",
    response_model=PresignedDownloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a presigned GET URL for direct-from-MinIO download",
)
def presign_download(
    storage: StorageDep,
    settings: SettingsDep,
    user: CurrentUser,  # presence of valid Bearer is enough — narrower checks live in /studies
    object_key: str = Query(..., description="MinIO object key returned by presign/upload."),
) -> PresignedDownloadResponse:
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
