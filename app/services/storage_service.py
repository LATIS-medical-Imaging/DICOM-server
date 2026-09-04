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
        # Internal client — server-to-MinIO operations inside Docker (minio:9000).
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

        external = urllib.parse.urlparse(settings.minio_external_endpoint)
        self._presign_client = Minio(
            external.netloc,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=external.scheme == "https",
            region="us-east-1",
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
    # Pre-signed URLs (browser ↔ MinIO direct I/O)
    # ------------------------------------------------------------------
    def presigned_put_url(self, bucket: str, key: str, expires_seconds: int | None = None) -> str:
        """Return a presigned PUT URL the client can use to upload directly to MinIO."""
        expires = timedelta(
            seconds=expires_seconds or self._settings.minio_presigned_url_expire_seconds
        )
        return self._presign_client.presigned_put_object(bucket, key, expires=expires)

    def presigned_get_url(self, bucket: str, key: str, expires_seconds: int | None = None) -> str:
        """Return a presigned GET URL the client can use to download directly from MinIO."""
        expires = timedelta(
            seconds=expires_seconds or self._settings.minio_presigned_url_expire_seconds
        )
        return self._presign_client.presigned_get_object(bucket, key, expires=expires)

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

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        """Fetch the full object content as bytes (server-side, e.g. processing pipeline)."""
        response = self.client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def get_object_bytes_or_none(self, bucket: str, key: str) -> bytes | None:
        """Fetch an object, returning None when it isn't there.

        Lets a cache probe be a single round-trip: a HEAD followed by a GET
        pays two, and the answer to "does it exist" is already carried by the
        GET's own 404.
        """
        try:
            return self.get_object_bytes(bucket, key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchBucket"):
                return None
            raise
