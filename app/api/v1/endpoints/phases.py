"""Series Phases endpoints.

Two routers live in this module:

* ``series_router`` — mounted at ``/series/{series_id}/phases``: list + create.
* ``phase_router``  — mounted at ``/phases/{phase_id}``: get + patch + delete.

Both are wired up in :mod:`app.api.v1.router`.  All endpoints require
``CurrentUser``; phase visibility is *private to creator* — owner check happens
in the service layer.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DBSession, SettingsDep, StorageDep
from app.db.models.series import Series
from app.schemas.phases import (
    AnnotationResponse,
    CreatePhaseRequest,
    PhaseListItem,
    PhaseListResponse,
    PhaseResponse,
    UpdatePhaseRequest,
)
from app.schemas.studies import InstanceResponse
from app.services.phase_service import PhaseService

series_router = APIRouter()
phase_router = APIRouter()


def _service(db: DBSession, storage: StorageDep, settings: SettingsDep) -> PhaseService:
    return PhaseService(db, storage, settings)


async def _build_response(service: PhaseService, phase: Series) -> PhaseResponse:
    """Compose the full PhaseResponse from a phase Series row.

    Centralised so create / get / patch all return the same shape — the viewer
    receives the merged instance stack and the annotation set in one call.
    """
    assert phase.parent_series_id is not None  # invariant: this is a phase row
    instances = await service.list_instances_rendered(phase)
    annotations = await service.list_phase_annotations(phase)
    return PhaseResponse(
        id=phase.id,
        parent_series_id=phase.parent_series_id,
        owner_id=phase.owner_id,
        name=phase.series_description or "",
        description=phase.protocol_name,
        instance_count=phase.instance_count,
        created_at=phase.created_at,
        updated_at=phase.updated_at,
        instances=[InstanceResponse.model_validate(i) for i in instances],
        annotations=[AnnotationResponse.model_validate(a) for a in annotations],
    )


# ── /series/{series_id}/phases ────────────────────────────────────────────


@series_router.get(
    "",
    response_model=PhaseListResponse,
    summary="List the caller's phases for a parent series",
)
async def list_phases(
    series_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
    storage: StorageDep,
    settings: SettingsDep,
) -> PhaseListResponse:
    service = _service(db, storage, settings)
    phases = await service.list_for_series(series_id, user.id)
    return PhaseListResponse(
        items=[
            PhaseListItem(
                id=p.id,
                parent_series_id=p.parent_series_id,
                owner_id=p.owner_id,
                name=p.series_description or "",
                instance_count=p.instance_count,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in phases
        ]
    )


@series_router.post(
    "",
    response_model=PhaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a new phase derived from a parent series",
)
async def create_phase(
    series_id: uuid.UUID,
    payload: CreatePhaseRequest,
    db: DBSession,
    user: CurrentUser,
    storage: StorageDep,
    settings: SettingsDep,
) -> PhaseResponse:
    service = _service(db, storage, settings)
    phase = await service.create_phase(series_id, user.id, payload)
    return await _build_response(service, phase)


# ── /phases/{phase_id} ────────────────────────────────────────────────────


@phase_router.get(
    "/{phase_id}",
    response_model=PhaseResponse,
    summary="Get a phase (merged stack + annotations)",
)
async def get_phase(
    phase_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
    storage: StorageDep,
    settings: SettingsDep,
) -> PhaseResponse:
    service = _service(db, storage, settings)
    phase = await service.get_phase(phase_id, user.id)
    return await _build_response(service, phase)


@phase_router.patch(
    "/{phase_id}",
    response_model=PhaseResponse,
    summary="In-place save (Save) or rename (omit `instances`)",
)
async def update_phase(
    phase_id: uuid.UUID,
    payload: UpdatePhaseRequest,
    db: DBSession,
    user: CurrentUser,
    storage: StorageDep,
    settings: SettingsDep,
) -> PhaseResponse:
    service = _service(db, storage, settings)
    phase = await service.update_phase(phase_id, user.id, payload)
    return await _build_response(service, phase)


@phase_router.delete(
    "/{phase_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a phase (derived blobs in MinIO are kept)",
)
async def delete_phase(
    phase_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
    storage: StorageDep,
    settings: SettingsDep,
) -> None:
    service = _service(db, storage, settings)
    await service.delete_phase(phase_id, user.id)
