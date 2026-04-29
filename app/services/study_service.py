"""Read queries for the study/series/instance hierarchy."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.instance import Instance
from app.db.models.series import Series
from app.db.models.study import Study


class StudyService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_studies(self, owner_id: uuid.UUID) -> tuple[list[Study], int]:
        """Returns (studies, total_count) for the given owner, excluding soft-deleted."""
        q = (
            select(Study)
            .where(Study.owner_id == owner_id, Study.deleted_at.is_(None))
            .order_by(Study.created_at.desc())
        )
        result = await self._db.execute(q)
        studies = list(result.scalars().all())

        count_result = await self._db.execute(
            select(func.count())
            .select_from(Study)
            .where(Study.owner_id == owner_id, Study.deleted_at.is_(None))
        )
        total = count_result.scalar_one()
        return studies, total

    async def get_study(self, study_id: uuid.UUID) -> Study | None:
        result = await self._db.execute(
            select(Study).where(Study.id == study_id, Study.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_series(self, study_id: uuid.UUID) -> list[Series]:
        result = await self._db.execute(
            select(Series).where(Series.study_id == study_id).order_by(Series.series_number)
        )
        return list(result.scalars().all())

    async def list_instances(self, series_id: uuid.UUID) -> list[Instance]:
        result = await self._db.execute(
            select(Instance)
            .where(Instance.series_id == series_id)
            .order_by(Instance.instance_number)
        )
        return list(result.scalars().all())
