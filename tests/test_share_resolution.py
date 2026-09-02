"""`active_share_row_for_series` must see series-level grants.

The study-only lookup (`active_share_row_for_study`) misses them, which made a
MANAGE grantee look read-only in the viewer and left "Remove from sidebar"
without a share id to revoke.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.db.models.share import SharePermission, ShareStatus
from app.services.share_service import ShareService


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeDb:
    """Captures the executed statement and replays a canned row set."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _FakeResult:
        self.statements.append(statement)
        return _FakeResult(self.rows)


class _Share:
    def __init__(
        self,
        permission: SharePermission,
        *,
        series_id: uuid.UUID | None = None,
        study_id: uuid.UUID | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.permission = permission
        self.series_id = series_id
        self.study_id = study_id
        self.status = ShareStatus.ACTIVE
        self.expires_at = datetime.now(UTC) + timedelta(days=1)
        self.grantor_id = uuid.uuid4()


class _Series:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.study_id = uuid.uuid4()


def _service(rows: list[Any]) -> tuple[ShareService, _FakeDb]:
    db = _FakeDb(rows)
    service = ShareService.__new__(ShareService)
    service._db = db  # type: ignore[attr-defined]
    return service, db


@pytest.mark.asyncio
async def test_series_level_share_is_found() -> None:
    series = _Series()
    share = _Share(SharePermission.MANAGE, series_id=series.id)
    service, _ = _service([share])

    found = await service.active_share_row_for_series(uuid.uuid4(), series)

    assert found is share
    assert found.permission == SharePermission.MANAGE


@pytest.mark.asyncio
async def test_no_share_returns_none() -> None:
    service, _ = _service([])

    assert await service.active_share_row_for_series(uuid.uuid4(), _Series()) is None


@pytest.mark.asyncio
async def test_most_permissive_row_wins() -> None:
    series = _Series()
    view = _Share(SharePermission.VIEW, study_id=series.study_id)
    manage = _Share(SharePermission.MANAGE, series_id=series.id)
    service, _ = _service([view, manage])

    assert await service.active_share_row_for_series(uuid.uuid4(), series) is manage


@pytest.mark.asyncio
async def test_series_row_preferred_over_equal_study_row() -> None:
    """Revoking should target the grant that actually surfaced the series."""
    series = _Series()
    study_share = _Share(SharePermission.ANNOTATE, study_id=series.study_id)
    series_share = _Share(SharePermission.ANNOTATE, series_id=series.id)
    service, _ = _service([study_share, series_share])

    found = await service.active_share_row_for_series(uuid.uuid4(), series)

    assert found is series_share


@pytest.mark.asyncio
async def test_query_covers_both_series_and_study_columns() -> None:
    """Guards the `or_` that the study-only variant is missing."""
    series = _Series()
    service, db = _service([])

    await service.active_share_row_for_series(uuid.uuid4(), series)

    sql = str(db.statements[0])
    assert "shares.series_id" in sql
    assert "shares.study_id" in sql
