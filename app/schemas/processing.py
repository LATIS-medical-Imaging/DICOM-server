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


class SegmentationModelInfo(BaseModel):
    """One checkpoint advertised by the remote model server."""

    name: str
    architecture: str
    loss: str
    patch_size: int
    dataset: str
    uses_clahe: bool


class ApplySegmentationRequest(BaseModel):
    """Body for `POST /processing/segmentation/apply`.

    `model_name` is free-form rather than a `Literal` because the model set
    is discovered from the model server at runtime, unlike the fixed
    classical-filter list above. `threshold` / `min_lesion_area` override the
    values embedded in the checkpoint's own config when set.
    """

    instance_id: uuid.UUID
    model_name: str
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    min_lesion_area: int | None = Field(default=None, ge=1)
    roi: ROI | None = None


class LesionAnnotation(BaseModel):
    """One detected lesion, as produced by `Annotation.to_dict()`.

    Coordinates are in source-image pixel space; the viewer converts them to
    Cornerstone world coordinates before drawing.
    """

    shape: Literal["RECTANGLE", "ELLIPSE", "POLYGON"]
    coordinates: list[Any]
    label: str
    center: list[float]
    bounding_box: list[int]
    metadata: dict[str, Any]


class ApplySegmentationResponse(BaseModel):
    download_url: str
    object_key: str
    model_name: str
    expires_in: int
    cached: bool
    lesion_count: int
    annotations: list[LesionAnnotation]
