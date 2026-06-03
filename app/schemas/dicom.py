"""Pydantic schemas for DICOM upload and download flows."""

from __future__ import annotations

from pydantic import BaseModel, Field


# Upload — single file
class PresignedUploadRequest(BaseModel):
    """Body sent by the client to request a presigned PUT URL.

    Note: ``owner_id`` is intentionally absent — it is derived server-side from
    the authenticated user (see ``/api/v1/presign/upload``).
    """

    study_instance_uid: str = Field(..., description="DICOM StudyInstanceUID.")
    series_instance_uid: str = Field(..., description="DICOM SeriesInstanceUID.")
    sop_instance_uid: str = Field(..., description="DICOM SOPInstanceUID (unique per file).")
    file_size_bytes: int = Field(..., gt=0, description="Exact byte size of the .dcm file.")


class PresignedUploadResponse(BaseModel):
    """Returned to the client so it can PUT the file directly to MinIO."""

    upload_url: str = Field(..., description="Presigned PUT URL valid for `expires_in` seconds.")
    object_key: str = Field(..., description="MinIO object key — store this to request downloads.")
    bucket: str
    expires_in: int = Field(..., description="Seconds until the presigned URL expires.")


# Upload — batch (one round-trip for multiple files)
class PresignedUploadBatchItem(BaseModel):
    """One file's DICOM identifiers within a batch presign request."""

    study_instance_uid: str = Field(..., description="DICOM StudyInstanceUID.")
    series_instance_uid: str = Field(..., description="DICOM SeriesInstanceUID.")
    sop_instance_uid: str = Field(..., description="DICOM SOPInstanceUID.")
    file_size_bytes: int = Field(..., gt=0)


class PresignedUploadBatchRequest(BaseModel):
    """Batch presign: one request → N presigned PUT URLs."""

    files: list[PresignedUploadBatchItem] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of files to presign. Max 500 per call.",
    )


class PresignedUploadBatchResponseItem(BaseModel):
    """Presigned PUT URL for one file. Index-aligned with the request list."""

    object_key: str
    upload_url: str
    bucket: str
    expires_in: int


class PresignedUploadBatchResponse(BaseModel):
    """Returned to the client; ``urls`` is index-aligned with the request ``files`` list."""

    urls: list[PresignedUploadBatchResponseItem]


# Download


class PresignedDownloadResponse(BaseModel):
    """Returned to the viewer so it can GET the file directly from MinIO."""

    download_url: str = Field(..., description="Presigned GET URL valid for `expires_in` seconds.")
    object_key: str
    expires_in: int
