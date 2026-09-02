"""Shared plumbing for endpoints that write derived pixel data.

`ProcessingService` (classical filters) and `SegmentationService` (deep
models) both download a source DICOM, run something over its pixels, and
write the result back under a content-addressed `derived/` key. The pieces
they have in common live here so neither owns the other's helpers.
"""

from __future__ import annotations

import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.instance import Instance
from app.schemas.processing import ROI

DERIVED_PREFIX = "derived"


class FilterError(ValueError):
    """Raised when a request is malformed (unknown filter/model, bad params)."""


async def load_instance(db: AsyncSession, instance_id: uuid.UUID) -> Instance:
    result = await db.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if instance is None:
        raise FilterError(f"Instance {instance_id} not found.")
    return instance


def clamp_roi(roi: ROI, shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    """Clip an ROI to image bounds and return (y0, x0, y1, x1) for numpy slicing."""
    rows, cols = shape[0], shape[1]
    x0 = max(0, min(roi.x, cols))
    y0 = max(0, min(roi.y, rows))
    x1 = max(x0, min(roi.x + roi.width, cols))
    y1 = max(y0, min(roi.y + roi.height, rows))
    return y0, x0, y1, x1


def rescale_to_dtype(processed: np.ndarray, target_dtype: np.dtype) -> np.ndarray:
    """Scale a float result back into the source's integer range.

    Filters return float tensors; DICOM pixel data needs to match the
    source dtype (typically uint16 for medical imaging). For binary
    outputs (Otsu, segmentation masks) we stretch {0,1} → full range so the
    result is still visible at the source's W/L.
    """
    if processed.dtype == target_dtype:
        return processed

    if not np.issubdtype(target_dtype, np.integer):
        # Keep float outputs as float32 if the source itself was float.
        return processed.astype(target_dtype, copy=False)

    info = np.iinfo(target_dtype)
    p_min = float(processed.min())
    p_max = float(processed.max())

    # Always promote to float64 before arithmetic — the input may be uint8
    # (e.g. BreastMask mask_only) and multiplying uint8 by int16.max overflows.
    f = processed.astype(np.float64)

    if p_max <= 1.0 and p_min >= 0.0:
        # Binary / normalised output — stretch to full range.
        scaled = f * info.max
    else:
        # Linear rescale preserves visual contrast across W/L presets.
        span = p_max - p_min if p_max > p_min else 1.0
        scaled = (f - p_min) / span * info.max

    return np.clip(scaled, info.min, info.max).astype(target_dtype, copy=False)
