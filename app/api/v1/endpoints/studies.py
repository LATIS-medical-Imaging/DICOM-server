"""Study, series, and instance metadata endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DBSession
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
    summary="List all studies for a user",
)
async def list_studies(
    db: DBSession,
    owner_id: Annotated[uuid.UUID, Query(description="UUID of the owning user.")],
) -> StudyListResponse:
    studies, total = await StudyService(db).list_studies(owner_id)
    return StudyListResponse(
        items=[StudyResponse.model_validate(s) for s in studies],
        total=total,
    )


@router.get(
    "/{study_id}",
    response_model=StudyResponse,
    summary="Get a single study",
)
async def get_study(study_id: uuid.UUID, db: DBSession) -> StudyResponse:
    study = await StudyService(db).get_study(study_id)
    if study is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found.")
    return StudyResponse.model_validate(study)


@router.get(
    "/{study_id}/series",
    response_model=list[SeriesResponse],
    summary="List all series in a study",
)
async def list_series(study_id: uuid.UUID, db: DBSession) -> list[SeriesResponse]:
    series = await StudyService(db).list_series(study_id)
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
) -> list[InstanceResponse]:
    instances = await StudyService(db).list_instances(series_id)
    return [InstanceResponse.model_validate(i) for i in instances]
