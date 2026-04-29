"""Upload job lifecycle — create a job row and enqueue the ingestion task."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.upload_job import UploadJob, UploadJobStatus


class UploadService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_job(
        self,
        owner_id: str,
        object_key: str,
        file_size_bytes: int,
    ) -> UploadJob:
        # Lazy import to avoid circular dependency at module load time.
        from app.workers.tasks.ingest import ingest_dicom_instance

        job = UploadJob(
            user_id=uuid.UUID(owner_id),
            job_type="dicom_upload",
            status=UploadJobStatus.PENDING,
            total_files=1,
            total_bytes=file_size_bytes,
        )
        self._db.add(job)
        await self._db.commit()
        await self._db.refresh(job)

        ingest_dicom_instance.delay(
            job_id=str(job.id),
            object_key=object_key,
            owner_id=owner_id,
            file_size_bytes=file_size_bytes,
        )

        return job
