"""Read schemas for the study/series/instance metadata endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel


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


class InstanceResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    series_id: uuid.UUID
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


class StudyListResponse(BaseModel):
    model_config = {"from_attributes": True}

    items: list[StudyResponse]
    total: int
