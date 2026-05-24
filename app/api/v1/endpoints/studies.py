"""Study, series, and instance metadata endpoints.

All endpoints require a valid access token. The current user is derived from
that token — never from a request parameter — and the ``StudyService``
visibility helpers enforce owner-or-share access control.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DBSession, SettingsDep, StorageDep
from app.core.exceptions import NotFoundError
from app.schemas.studies import (
    InstanceResponse,
    SeriesResponse,
    StudyListResponse,
    StudyResponse,
)
from app.services.phase_service import PhaseService
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
    summary="List all instances in a series (phase-aware merged stack)",
)
async def list_instances(
    study_id: uuid.UUID,
    series_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
    storage: StorageDep,
    settings: SettingsDep,
) -> list[InstanceResponse]:
    """Return the ordered instance stack.

    For an original series this is identical to its row-set ordered by
    ``instance_number``.  For a phase, the parent's instances are merged with
    the phase's overrides spliced in at the matching ``instance_number`` —
    the viewer doesn't need to know which kind it loaded.
    """
    service = StudyService(db)
    await service.get_visible_study(study_id, user.id)

    series = await service.get_series(series_id)
    if series is None or series.study_id != study_id:
        raise NotFoundError("Series not found.")

    # Phases enforce their own private-to-owner visibility on top of the
    # parent-study visibility we already checked.
    if series.parent_series_id is not None and series.owner_id != user.id:
        raise NotFoundError("Series not found.")

    phases = PhaseService(db, storage, settings)
    instances = await phases.list_instances_rendered(series)
    return [InstanceResponse.model_validate(i) for i in instances]


@router.delete(
    "/{study_id}/series/{series_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an original series (study owner) or a phase (creator only)",
)
async def delete_series(
    study_id: uuid.UUID,
    series_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
) -> None:
    service = StudyService(db)
    study = await service.get_visible_study(study_id, user.id)

    series = await service.get_series(series_id)
    if series is None or series.study_id != study_id:
        raise NotFoundError("Series not found.")

    if series.parent_series_id is None:
        # Original series — owner_id is NULL; ownership belongs to the study.
        # Only the study owner may delete an original (and its cascaded phases).
        if study.owner_id != user.id:
            raise NotFoundError("Series not found.")
    else:
        # Phase — private to creator; owner_id is the doctor who saved it.
        if series.owner_id != user.id:
            raise NotFoundError("Series not found.")

    await service.delete_series(series)
