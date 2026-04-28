"""Pydantic schemas for DICOM upload and download flows."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Upload
class PresignedUploadRequest(BaseModel):
    """Body sent by the client to request a presigned PUT URL."""

    owner_id: str = Field(..., description="UUID of the user who owns this file.")
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


# Download
class PresignedDownloadResponse(BaseModel):
    """Returned to the viewer so it can GET the file directly from MinIO."""

    download_url: str = Field(..., description="Presigned GET URL valid for `expires_in` seconds.")
    object_key: str
    expires_in: int
