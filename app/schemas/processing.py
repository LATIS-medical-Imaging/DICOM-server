"""Request/response models for the image-processing endpoints."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# Heavy algorithms only (medical-image-std filters).
FilterName = Literal[
    "top_hat",
    "kmeans",
    "fcm",
    "pfcm",
    "febds",
    "breast_mask",
]


class ApplyFilterRequest(BaseModel):
    """Body for `POST /processing/apply`.

    `params` is intentionally loose (a free-form dict): every filter has
    different knobs and validation lives inside `ProcessingService` where
    the dispatch table is. Keeping it loose here avoids ten near-identical
    sibling schemas just to express which keys each filter accepts.
    """

    instance_id: uuid.UUID
    filter: FilterName
    params: dict[str, Any] = Field(default_factory=dict)


class ApplyFilterResponse(BaseModel):
    download_url: str
    object_key: str
    filter: FilterName
    expires_in: int
    cached: bool
