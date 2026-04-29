"""Pydantic schemas for upload job creation and status polling."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateUploadJobRequest(BaseModel):
    """Posted by the client after it has PUT the .dcm file to MinIO."""

    owner_id: str = Field(..., description="UUID of the uploading user.")
    object_key: str = Field(..., description="MinIO object key returned by presign-upload.")
    file_size_bytes: int = Field(..., gt=0, description="Exact byte size of the .dcm file.")


class UploadJobResponse(BaseModel):
    """Returned by both POST /uploads and GET /uploads/{job_id}."""

    model_config = {"from_attributes": True}

    job_id: str = Field(alias="id", serialization_alias="job_id")
    status: str
    total_files: int
    processed_files: int
    failed_files: int
    total_bytes: int
    processed_bytes: int
    errors: list[dict[str, Any]]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}
