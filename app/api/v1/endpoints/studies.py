"""Study, series, and instance metadata endpoints.

All endpoints require a valid access token. The current user is derived from
that token — never from a request parameter — and the ``StudyService``
visibility helpers enforce owner-or-share access control.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, DBSession
from app.core.exceptions import NotFoundError
from app.schemas.studies import (
    InstanceResponse,
    SeriesResponse,
    StudyListResponse,
    StudyResponse,
)
from app.services.study_service import StudyService

router = APIRouter()


@router.get(
    "",
    response_model=StudyListResponse,
    summary="List studies visible to the authenticated user",
)
async def list_studies(db: DBSession, user: CurrentUser) -> StudyListResponse:
    studies, total = await StudyService(db).list_visible(user.id)
    return StudyListResponse(
        items=[StudyResponse.model_validate(s) for s in studies],
        total=total,
    )


@router.get(
    "/{study_id}",
    response_model=StudyResponse,
    summary="Get a single study (must be owned by or shared with the caller)",
)
async def get_study(study_id: uuid.UUID, db: DBSession, user: CurrentUser) -> StudyResponse:
    study = await StudyService(db).get_visible_study(study_id, user.id)
    return StudyResponse.model_validate(study)


@router.get(
    "/{study_id}/series",
    response_model=list[SeriesResponse],
    summary="List all series in a study",
)
async def list_series(
    study_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
) -> list[SeriesResponse]:
    service = StudyService(db)
    await service.get_visible_study(study_id, user.id)  # 404 if not visible
    series = await service.list_series(study_id)
    return [SeriesResponse.model_validate(s) for s in series]


@router.get(
    "/{study_id}/series/{series_id}/instances",
    response_model=list[InstanceResponse],
    summary="List all instances in a series",
)
async def list_instances(
    study_id: uuid.UUID,
    series_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
) -> list[InstanceResponse]:
    service = StudyService(db)
    await service.get_visible_study(study_id, user.id)

    series = await service.get_series(series_id)
    if series is None or series.study_id != study_id:
        raise NotFoundError("Series not found.")

    instances = await service.list_instances(series_id)
    return [InstanceResponse.model_validate(i) for i in instances]
