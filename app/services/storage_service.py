"""Thin wrapper around the MinIO Python client.

Centralizes bucket naming, object key layout, and pre-signed URL generation
so routes/services never import ``minio`` directly.
"""

from __future__ import annotations

from datetime import timedelta
from typing import IO

from minio import Minio
from minio.error import S3Error

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class StorageService:
    """Object storage abstraction backed by MinIO (or any S3-compatible store)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    # ------------------------------------------------------------------
    # Bucket lifecycle
    # ------------------------------------------------------------------
    def ensure_buckets(self) -> None:
        """Create application buckets if they don't already exist."""
        for bucket in (
            self._settings.minio_bucket_dicom,
            self._settings.minio_bucket_thumbnails,
        ):
            try:
                if not self.client.bucket_exists(bucket):
                    self.client.make_bucket(bucket)
                    logger.info("minio_bucket_created", bucket=bucket)
            except S3Error as exc:
                logger.error("minio_bucket_error", bucket=bucket, error=str(exc))
                raise

    # ------------------------------------------------------------------
    # Key layout
    # ------------------------------------------------------------------
    @staticmethod
    def dicom_object_key(
        owner_id: str,
        study_uid: str,
        series_uid: str,
        sop_uid: str,
    ) -> str:
        """``{owner_id}/{study_uid}/{series_uid}/{sop_uid}.dcm`` — matches the architecture."""
        return f"{owner_id}/{study_uid}/{series_uid}/{sop_uid}.dcm"

    # ------------------------------------------------------------------
    # Uploads / downloads
    # ------------------------------------------------------------------
    def put_object(
        self,
        bucket: str,
        key: str,
        data: IO[bytes],
        length: int,
        content_type: str = "application/dicom",
    ) -> None:
        self.client.put_object(bucket, key, data, length, content_type=content_type)

    def presigned_put_url(self, bucket: str, key: str, expires_seconds: int | None = None) -> str:
        expires = timedelta(
            seconds=expires_seconds or self._settings.minio_presigned_url_expire_seconds
        )
        return self.client.presigned_put_object(bucket, key, expires=expires)

    def presigned_get_url(self, bucket: str, key: str, expires_seconds: int | None = None) -> str:
        expires = timedelta(
            seconds=expires_seconds or self._settings.minio_presigned_url_expire_seconds
        )
        return self.client.presigned_get_object(bucket, key, expires=expires)

    def remove_object(self, bucket: str, key: str) -> None:
        self.client.remove_object(bucket, key)
