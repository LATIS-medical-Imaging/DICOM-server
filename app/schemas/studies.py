"""Read schemas for the study/series/instance metadata endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.schemas.shares import ShareSourceDto


class SeriesResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    study_id: uuid.UUID
    series_instance_uid: str
    series_number: int | None
    modality: str
    series_description: str | None
    body_part_examined: str | None
    protocol_name: str | None
    slice_thickness: Decimal | None
    pixel_spacing: str | None
    instance_count: int
    size_bytes: int
    storage_path: str
    created_at: datetime
    # Null when the caller owns the series. Set from the share that grants
    # access — series-level if there is one, otherwise the parent study's.
    # Forward ref resolved by ``SeriesResponse.model_rebuild`` in shares.py.
    share_source: ShareSourceDto | None = None


class InstanceResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    series_id: uuid.UUID
    parent_instance_id: uuid.UUID | None = None
    sop_instance_uid: str
    sop_class_uid: str | None
    instance_number: int | None
    rows: int | None
    columns: int | None
    bits_allocated: int | None
    bits_stored: int | None
    number_of_frames: int | None
    window_center: Decimal | None
    window_width: Decimal | None
    rescale_intercept: Decimal | None
    rescale_slope: Decimal | None
    transfer_syntax_uid: str | None
    file_path: str
    file_size_bytes: int
    created_at: datetime


class StudyResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    owner_id: uuid.UUID
    patient_id: uuid.UUID
    study_instance_uid: str
    accession_number: str | None
    study_date: date | None
    study_time: time | None
    study_description: str | None
    modality: str | None
    referring_physician: str | None
    institution_name: str | None
    total_series_count: int
    total_instance_count: int
    total_size_bytes: int
    status: str
    storage_path: str
    created_at: datetime
    updated_at: datetime
    # Populated by the studies endpoint when the caller is *not* the owner —
    # carries the share row + grantor + permission so the frontend's sidebar
    # can render the "Shared by Dr X" subtitle and the viewer can gate write
    # actions on permission.  None for owned studies.
    # Forward ref resolved by ``StudyResponse.model_rebuild`` in shares.py.
    share_source: ShareSourceDto | None = None


class StudyListResponse(BaseModel):
    model_config = {"from_attributes": True}

    items: list[StudyResponse]
    total: int


# ── Aggregated viewer response ─────────────────────────────────────────────
# Returned by GET /studies/{id}/viewer — replaces the N+1 chain of
# /series → /instances → /presign/download with a single authenticated call.


class ViewerInstanceResponse(InstanceResponse):
    """Instance metadata + a ready-to-use presigned GET URL for the pixel data."""

    download_url: str
    expires_in: int


class ViewerSeriesResponse(SeriesResponse):
    """Series metadata with all its instances (and their download URLs) embedded."""

    instances: list[ViewerInstanceResponse]


class ViewerStudyResponse(StudyResponse):
    """Full study payload: metadata + every series + every instance + presigned URLs.

    Replaces the client-side N+1 loop (list series → per-series list instances →
    per-instance presign/download) with one authenticated round-trip.
    """

    series: list[ViewerSeriesResponse]
