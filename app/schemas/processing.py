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


class ROI(BaseModel):
    """Rectangular region of interest, in image pixel coordinates.

    Origin is the top-left of the image. `x`/`y` may be negative (drag
    started outside the image) and the rectangle may extend past the right
    or bottom edge — `ProcessingService._clamp_roi` clips both ends to the
    image bounds before slicing. Only the size has to be strictly positive.
    """

    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ApplyFilterRequest(BaseModel):
    """Body for `POST /processing/apply`.

    `params` is intentionally loose (a free-form dict): every filter has
    different knobs and validation lives inside `ProcessingService` where
    the dispatch table is. Keeping it loose here avoids ten near-identical
    sibling schemas just to express which keys each filter accepts.

    `roi` is optional — when set, the filter runs only inside the rectangle
    and the rest of the image is left untouched.
    """

    instance_id: uuid.UUID
    filter: FilterName
    params: dict[str, Any] = Field(default_factory=dict)
    roi: ROI | None = None


class ApplyFilterResponse(BaseModel):
    download_url: str
    object_key: str
    filter: FilterName
    expires_in: int
    cached: bool
