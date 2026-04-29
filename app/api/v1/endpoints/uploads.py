"""Upload job endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DBSession
from app.db.models.upload_job import UploadJob
from app.schemas.uploads import CreateUploadJobRequest, UploadJobResponse
from app.services.upload_service import UploadService

router = APIRouter()


@router.post(
    "",
    response_model=UploadJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an upload job and enqueue DICOM ingestion",
)
async def create_upload_job(
    body: CreateUploadJobRequest,
    db: DBSession,
) -> UploadJobResponse:
    job = await UploadService(db).create_job(
        owner_id=body.owner_id,
        object_key=body.object_key,
        file_size_bytes=body.file_size_bytes,
    )
    return UploadJobResponse.model_validate(job)


@router.get(
    "/{job_id}",
    response_model=UploadJobResponse,
    summary="Poll upload job status",
)
async def get_upload_job(
    job_id: str,
    db: DBSession,
) -> UploadJobResponse:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="job_id must be a valid UUID.",
        ) from exc

    result = await db.execute(select(UploadJob).where(UploadJob.id == job_uuid))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job '{job_id}' not found.",
        )

    return UploadJobResponse.model_validate(job)
