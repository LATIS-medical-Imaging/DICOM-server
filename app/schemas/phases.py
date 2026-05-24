"""Pydantic schemas for the Series Phases module.

A *phase* is a series row with ``parent_series_id`` set: a named snapshot of
the modifications (server-filtered pixels, annotations) a doctor saved against
the original series.  The render-time view of a phase is the union of the
parent's instances with the phase's overrides spliced in by ``instance_number``.

Wire format mirrors the rest of the API: snake_case throughout, ``UUID`` for
ids, ``model_config = {"from_attributes": True}`` for ORM compatibility.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.studies import InstanceResponse

# ── Annotation payload ─────────────────────────────────────────────────────


class AnnotationPayload(BaseModel):
    """One annotation as the client sends it on save.

    Mirrors the columns of the ``annotations`` table.  ``instance_id`` is
    omitted on the wire — annotations are nested under their parent-instance
    entry in :class:`PhaseInstancePayload`, and the service fills the FK from
    the phase Instance row it creates.
    """

    cornerstone_uid: str | None = None
    tool_type: str = Field(..., min_length=1, max_length=50)
    annotation_data: dict[str, Any]
    viewport_state: dict[str, Any] = Field(default_factory=dict)

    measurement_value: Decimal | None = None
    measurement_unit: str | None = Field(default=None, max_length=20)
    measurement_area: Decimal | None = None
    measurement_mean: Decimal | None = None
    measurement_stddev: Decimal | None = None

    label: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=7)
    is_visible: bool = True
    is_locked: bool = False


class AnnotationResponse(BaseModel):
    """One annotation as the client receives it on load."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    instance_id: uuid.UUID
    user_id: uuid.UUID

    cornerstone_uid: str | None
    tool_type: str
    annotation_data: dict[str, Any]
    viewport_state: dict[str, Any]

    measurement_value: Decimal | None
    measurement_unit: str | None
    measurement_area: Decimal | None
    measurement_mean: Decimal | None
    measurement_stddev: Decimal | None

    label: str | None
    color: str | None
    is_visible: bool
    is_locked: bool

    created_at: datetime
    updated_at: datetime


# ── Phase create / update payloads ─────────────────────────────────────────


class PhaseInstancePayload(BaseModel):
    """One "touched" slice in a save payload.

    ``parent_instance_id`` identifies which slice of the parent series the
    modification targets — the service copies its DICOM metadata onto the new
    phase Instance row.

    ``derived_object_key`` is the MinIO key of the filter-result blob; when
    ``None`` the slice's pixel data is unchanged (annotation-only) and the
    phase Instance row will point at the parent's ``file_path``.
    """

    parent_instance_id: uuid.UUID
    derived_object_key: str | None = Field(default=None, max_length=500)
    applied_filter: str | None = Field(default=None, max_length=64)
    applied_filter_params: dict[str, Any] = Field(default_factory=dict)
    annotations: list[AnnotationPayload] = Field(default_factory=list)


class CreatePhaseRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    instances: list[PhaseInstancePayload] = Field(..., min_length=1)


class UpdatePhaseRequest(BaseModel):
    """In-place save.

    When ``instances`` is provided the service deletes the phase's existing
    Instance rows (cascade-deletes their annotations) and re-inserts the new
    set atomically.  Omitting ``instances`` makes this a pure rename.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    instances: list[PhaseInstancePayload] | None = None


# ── Phase responses ────────────────────────────────────────────────────────


class PhaseListItem(BaseModel):
    """Sidebar entry — light payload, no instances / annotations.

    ``name`` is set explicitly from ``series_description`` in the endpoint so
    we avoid an ORM-alias round-trip that breaks FastAPI's ``by_alias`` JSON
    serialisation (the alias would become the JSON key instead of ``name``).
    """

    id: uuid.UUID
    parent_series_id: uuid.UUID
    owner_id: uuid.UUID | None
    name: str
    instance_count: int
    created_at: datetime
    updated_at: datetime


class PhaseListResponse(BaseModel):
    items: list[PhaseListItem]


class PhaseResponse(BaseModel):
    """Full phase payload returned by GET /phases/{id}, POST, and PATCH.

    ``instances`` is the **merged** stack (parent + phase overrides, ordered
    by ``instance_number``) so the viewer can call ``viewport.setStack``
    without any client-side splicing.  ``annotations`` is the flat list of
    all annotations belonging to this phase, keyed off ``instance_id``
    (which points at phase-owned Instance rows in the merged list).
    """

    id: uuid.UUID
    parent_series_id: uuid.UUID
    owner_id: uuid.UUID | None
    name: str
    description: str | None
    instance_count: int
    created_at: datetime
    updated_at: datetime

    instances: list[InstanceResponse]
    annotations: list[AnnotationResponse]
