"""Read queries for the study/series/instance hierarchy.

Visibility model: a user can read a study iff they own it OR there is an active
non-expired ``Share`` row pointing at the study (or an ancestor). The
``list_visible`` and ``assert_can_read`` helpers encapsulate that rule so every
endpoint enforces it identically — adding more granular share types later
(series-level, instance-level) only requires extending the helpers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.models.instance import Instance
from app.db.models.series import Series
from app.db.models.share import Share, ShareStatus
from app.db.models.study import Study


def _active_share_filter(
    user_id: uuid.UUID, now: datetime
) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[bool]]:
    """Predicate for an active, non-expired share granted *to* ``user_id``."""
    return (
        Share.grantee_id == user_id,
        Share.status == ShareStatus.ACTIVE,
        or_(Share.expires_at.is_(None), Share.expires_at > now),
    )


class StudyService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_visible(self, user_id: uuid.UUID) -> tuple[list[Study], int]:
        """Return ``(studies, total)`` visible to ``user_id``.

        Visible = owns the study, OR has an active study-level share, OR has a
        series/instance share whose study is the row.
        """
        now = datetime.now(UTC)
        share_predicates = _active_share_filter(user_id, now)

        # Build the set of study ids reachable through shares (study/series/instance).
        share_subq = (
            select(Study.id)
            .outerjoin(Series, Series.study_id == Study.id)
            .outerjoin(Instance, Instance.series_id == Series.id)
            .join(
                Share,
                or_(
                    Share.study_id == Study.id,
                    Share.series_id == Series.id,
                    Share.instance_id == Instance.id,
                ),
            )
            .where(*share_predicates)
        )

        base = (
            select(Study)
            .where(
                Study.deleted_at.is_(None),
                or_(Study.owner_id == user_id, Study.id.in_(share_subq)),
            )
            .order_by(Study.created_at.desc())
        )
        result = await self._db.execute(base)
        studies = list({s.id: s for s in result.scalars().all()}.values())

        count_q = (
            select(func.count(func.distinct(Study.id)))
            .select_from(Study)
            .where(
                Study.deleted_at.is_(None),
                or_(Study.owner_id == user_id, Study.id.in_(share_subq)),
            )
        )
        total = (await self._db.execute(count_q)).scalar_one()
        return studies, total

    async def get_study(self, study_id: uuid.UUID) -> Study | None:
        result = await self._db.execute(
            select(Study).where(Study.id == study_id, Study.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_visible_study(self, study_id: uuid.UUID, user_id: uuid.UUID) -> Study:
        """Return a study *only* if ``user_id`` can read it.

        Raises :class:`NotFoundError` otherwise — never 403, to avoid leaking
        the existence of studies the caller can't see.
        """
        study = await self.get_study(study_id)
        if study is None:
            raise NotFoundError("Study not found.")
        if not await self.can_read_study(study, user_id):
            raise NotFoundError("Study not found.")
        return study

    async def can_read_study(self, study: Study, user_id: uuid.UUID) -> bool:
        if study.owner_id == user_id:
            return True
        now = datetime.now(UTC)
        share_predicates = _active_share_filter(user_id, now)
        q = (
            select(Share.id)
            .outerjoin(Series, Series.study_id == study.id)
            .outerjoin(Instance, Instance.series_id == Series.id)
            .where(
                *share_predicates,
                or_(
                    Share.study_id == study.id,
                    Share.series_id == Series.id,
                    Share.instance_id == Instance.id,
                ),
            )
            .limit(1)
        )
        return (await self._db.execute(q)).scalar_one_or_none() is not None

    async def list_series(
        self,
        study_id: uuid.UUID,
        viewer_id: uuid.UUID | None = None,
    ) -> list[Series]:
        """List the original DICOM-ingested series for a study.

        Phases (``parent_series_id IS NOT NULL``) live in the same table but
        belong to a separate endpoint (``GET /series/{id}/phases``) so the
        sidebar's top-level list stays clean.

        When ``viewer_id`` is supplied and they're not the study owner, the
        result is filtered to only series the viewer has explicit visibility
        on: a study-level share returns all series; a series-level share
        returns only those series.  Owners (and callers passing
        ``viewer_id=None``) get every original series.
        """
        if viewer_id is not None:
            study = await self.get_study(study_id)
            if study is not None and study.owner_id != viewer_id:
                visible_ids = await self._visible_series_ids(study_id, viewer_id)
                if visible_ids is None:
                    # Caller has no explicit series-level scoping → study-level
                    # share (or no share at all, in which case endpoint-level
                    # visibility already rejected them).
                    pass
                else:
                    result = await self._db.execute(
                        select(Series)
                        .where(
                            Series.study_id == study_id,
                            Series.parent_series_id.is_(None),
                            Series.id.in_(visible_ids),
                        )
                        .order_by(Series.series_number)
                    )
                    return list(result.scalars().all())

        result = await self._db.execute(
            select(Series)
            .where(
                Series.study_id == study_id,
                Series.parent_series_id.is_(None),
            )
            .order_by(Series.series_number)
        )
        return list(result.scalars().all())

    async def _visible_series_ids(
        self,
        study_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[uuid.UUID] | None:
        """Return the set of series ids in ``study_id`` reachable via active
        series-scoped shares for ``user_id``, or ``None`` if a study-level
        share grants the caller everything (so no filter is needed).
        """
        # Local imports to avoid cycles — this helper is read-only on Share.
        from app.db.models.share import Share, ShareStatus

        now = datetime.now(UTC)
        # If a study-level active share exists, no series filter is needed.
        study_share = (
            await self._db.execute(
                select(Share.id).where(
                    Share.grantee_id == user_id,
                    Share.study_id == study_id,
                    Share.status == ShareStatus.ACTIVE,
                    or_(Share.expires_at.is_(None), Share.expires_at > now),
                )
            )
        ).first()
        if study_share is not None:
            return None  # caller sees every series

        # Otherwise: only series-scoped shares.
        rows = await self._db.execute(
            select(Series.id)
            .join(Share, Share.series_id == Series.id)
            .where(
                Series.study_id == study_id,
                Share.grantee_id == user_id,
                Share.status == ShareStatus.ACTIVE,
                or_(Share.expires_at.is_(None), Share.expires_at > now),
            )
        )
        return [row[0] for row in rows.all()]

    async def list_instances(self, series_id: uuid.UUID) -> list[Instance]:
        result = await self._db.execute(
            select(Instance).where(Instance.series_id == series_id)
            # InstanceNumber is absent from plenty of real files; without a
            # tiebreaker those rows come back in whatever order Postgres
            # happens to scan, so the stack reshuffles between loads.
            .order_by(Instance.instance_number.nulls_last(), Instance.created_at, Instance.id)
        )
        return list(result.scalars().all())

    async def get_series(self, series_id: uuid.UUID) -> Series | None:
        result = await self._db.execute(select(Series).where(Series.id == series_id))
        return result.scalar_one_or_none()

    async def get_instance(self, instance_id: uuid.UUID) -> Instance | None:
        result = await self._db.execute(select(Instance).where(Instance.id == instance_id))
        return result.scalar_one_or_none()

    async def delete_series(self, series: Series) -> None:
        """Delete a series and all its instances. (Cascades to phases and their instances too.)"""
        await self._db.delete(series)
        await self._db.commit()
