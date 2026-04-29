from __future__ import annotations

import urllib.parse
from datetime import timedelta
from typing import BinaryIO

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
        """`{owner_id}/{study_uid}/{series_uid}/{sop_uid}.dcm` — matches the architecture."""
        return f"{owner_id}/{study_uid}/{series_uid}/{sop_uid}.dcm"

    # ------------------------------------------------------------------
    # Internal → external URL rewrite
    # ------------------------------------------------------------------
    def _to_external_url(self, internal_url: str) -> str:
        """Replace the internal Docker hostname with the externally reachable endpoint.

        MinIO generates presigned URLs using the client's configured endpoint
        (e.g. ``minio:9000``). The browser can't reach that hostname, so we
        swap in ``minio_external_endpoint`` (e.g. ``http://localhost:9000``).
        """
        parsed = urllib.parse.urlparse(internal_url)
        external = urllib.parse.urlparse(self._settings.minio_external_endpoint)
        rewritten = parsed._replace(scheme=external.scheme, netloc=external.netloc)
        return urllib.parse.urlunparse(rewritten)

    # ------------------------------------------------------------------
    # Pre-signed URLs (browser ↔ MinIO direct I/O)
    # ------------------------------------------------------------------
    def presigned_put_url(self, bucket: str, key: str, expires_seconds: int | None = None) -> str:
        """Return a presigned PUT URL the client can use to upload directly to MinIO."""
        expires = timedelta(
            seconds=expires_seconds or self._settings.minio_presigned_url_expire_seconds
        )
        url = self.client.presigned_put_object(bucket, key, expires=expires)
        return self._to_external_url(url)

    def presigned_get_url(self, bucket: str, key: str, expires_seconds: int | None = None) -> str:
        """Return a presigned GET URL the client can use to download directly from MinIO."""
        expires = timedelta(
            seconds=expires_seconds or self._settings.minio_presigned_url_expire_seconds
        )
        url = self.client.presigned_get_object(bucket, key, expires=expires)
        return self._to_external_url(url)

    # ------------------------------------------------------------------
    # Direct object I/O (server-side, e.g. Celery workers)
    # ------------------------------------------------------------------
    def put_object(
        self,
        bucket: str,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str = "application/dicom",
    ) -> None:
        self.client.put_object(bucket, key, data, length, content_type=content_type)

    def remove_object(self, bucket: str, key: str) -> None:
        self.client.remove_object(bucket, key)

    def object_exists(self, bucket: str, key: str) -> bool:
        """Return True if the object exists in the bucket."""
        try:
            self.client.stat_object(bucket, key)
            return True
        except S3Error:
            return False
