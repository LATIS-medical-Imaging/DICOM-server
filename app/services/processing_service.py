"""Apply `medical-image-std` filters to a stored DICOM instance.

The pipeline downloads the source DICOM from object storage, runs a CPU
filter via `medical-image-std`, and writes a derived DICOM back under a
deterministic key. Identical (filter, params) requests therefore hit the
existing object instead of recomputing — the endpoint can be called
repeatedly while the user toggles filters in the viewer.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from collections.abc import Callable
from typing import Any

import numpy as np
import pydicom
from pydicom.uid import ExplicitVRLittleEndian
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models.instance import Instance
from app.services.storage_service import StorageService

logger = get_logger(__name__)


_DERIVED_PREFIX = "derived"


class FilterError(ValueError):
    """Raised when a filter request is malformed (unknown filter, bad params)."""


class ProcessingService:
    """Server-side filter pipeline.

    Lazily imports `medical-image-std` so unit tests that don't touch
    processing don't pay the torch import cost on collection.
    """

    def __init__(self, db: AsyncSession, storage: StorageService, settings: Settings) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings

    async def apply(
        self,
        instance_id: uuid.UUID,
        filter_name: str,
        params: dict[str, Any],
    ) -> tuple[str, bool]:
        """Apply a filter to the instance's pixel data.

        Returns (derived_object_key, was_cached).
        """
        instance = await self._load_instance(instance_id)
        source_key = instance.file_path
        derived_key = self._derived_key(source_key, filter_name, params)
        bucket = self._settings.minio_bucket_dicom

        if self._storage.object_exists(bucket, derived_key):
            return derived_key, True

        source_bytes = self._storage.get_object_bytes(bucket, source_key)
        derived_bytes = self._run_filter(source_bytes, filter_name, params)

        buffer = io.BytesIO(derived_bytes)
        self._storage.put_object(
            bucket=bucket,
            key=derived_key,
            data=buffer,
            length=len(derived_bytes),
            content_type="application/dicom",
        )
        logger.info(
            "processing_filter_applied",
            instance_id=str(instance_id),
            filter=filter_name,
            derived_key=derived_key,
            size=len(derived_bytes),
        )
        return derived_key, False

    async def _load_instance(self, instance_id: uuid.UUID) -> Instance:
        result = await self._db.execute(select(Instance).where(Instance.id == instance_id))
        instance = result.scalar_one_or_none()
        if instance is None:
            raise FilterError(f"Instance {instance_id} not found.")
        return instance

    @staticmethod
    def _derived_key(source_key: str, filter_name: str, params: dict[str, Any]) -> str:
        """Content-addressed key so repeat requests reuse the prior result.

        Example: `{owner}/{study}/{series}/{sop}.dcm` →
        `{owner}/{study}/{series}/derived/{sop}--gaussian-{hash}.dcm`
        """
        prefix, _, filename = source_key.rpartition("/")
        sop = filename.removesuffix(".dcm") or filename
        normalized = json.dumps(params, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{filter_name}|{normalized}".encode()).hexdigest()[:12]
        return f"{prefix}/{_DERIVED_PREFIX}/{sop}--{filter_name}-{digest}.dcm"

    def _run_filter(self, source_bytes: bytes, filter_name: str, params: dict[str, Any]) -> bytes:
        """Decode → apply filter via `medical-image-std` → re-encode as DICOM."""
        ds = pydicom.dcmread(io.BytesIO(source_bytes))
        try:
            original = ds.pixel_array  # numpy, native dtype (typically uint16)
        except RuntimeError as exc:
            # pydicom raises RuntimeError when the transfer syntax needs a plugin
            # that isn't installed (e.g. JPEG Lossless requires pylibjpeg).
            raise FilterError(f"Cannot decode pixel data: {exc}") from exc
        if original.ndim != 2:
            # We only handle single-frame 2D images here. Multi-frame stacks
            # come through this endpoint one instance at a time.
            raise FilterError("Only single-frame 2D images are supported for filtering.")

        processed = _apply_to_array(original, filter_name, params)
        scaled = _rescale_to_dtype(processed, original.dtype)

        # Switch to uncompressed transfer syntax before writing back raw pixel
        # bytes — if the source used a compressed syntax (e.g. JPEG Lossless),
        # pydicom would try to encapsulate the raw bytes and fail.
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.PixelData = scaled.tobytes()
        ds.Rows = scaled.shape[0]
        ds.Columns = scaled.shape[1]

        out = io.BytesIO()
        ds.save_as(out, write_like_original=False)
        return out.getvalue()


def _apply_to_array(
    pixels: np.ndarray,
    filter_name: str,
    params: dict[str, Any],
) -> np.ndarray:
    """Dispatch to a `medical-image-std` operation and return the result as numpy.

    Each handler accepts the raw numpy array and returns a numpy float
    array — final dtype conversion happens in `_rescale_to_dtype`.
    """
    # Imports are local to keep cold-start fast for non-filter requests.
    import torch  # noqa: F401  (resolves before medical_image to surface a clean error)
    from medical_image import (
        BreastMaskAlgorithm,
        FCMAlgorithm,
        FebdsAlgorithm,
        InMemoryImage,
        KMeansAlgorithm,
        PFCMAlgorithm,
        TopHatAlgorithm,
    )

    src = InMemoryImage(array=pixels.astype(np.float32))
    out = InMemoryImage(source_image=src)

    handlers: dict[str, Callable[[], object]] = {
        "top_hat": lambda: TopHatAlgorithm(radius=int(params.get("radius", 4)), device="cpu").apply(
            src, out
        ),
        "kmeans": lambda: KMeansAlgorithm(k=int(params.get("k", 2)), device="cpu").apply(src, out),
        "fcm": lambda: FCMAlgorithm(c=int(params.get("c", 2)), device="cpu").apply(src, out),
        "pfcm": lambda: PFCMAlgorithm(c=int(params.get("c", 2)), device="cpu").apply(src, out),
        "febds": lambda: FebdsAlgorithm(
            method=str(params.get("method", "dog")), device="cpu"
        ).apply(src, out),
        "breast_mask": lambda: BreastMaskAlgorithm(
            mask_only=bool(params.get("mask_only", False)), device="cpu"
        ).apply(src, out),
    }

    handler = handlers.get(filter_name)
    if handler is None:
        raise FilterError(f"Unknown filter: {filter_name!r}")

    try:
        handler()
    except Exception as exc:
        raise FilterError(f"Filter '{filter_name}' failed: {exc}") from exc

    if out.pixel_data is None:
        raise FilterError(f"Filter '{filter_name}' produced no output.")

    return out.pixel_data.detach().cpu().numpy()


def _rescale_to_dtype(processed: np.ndarray, target_dtype: np.dtype) -> np.ndarray:
    """Scale a float result back into the source's integer range.

    Filters return float tensors; DICOM pixel data needs to match the
    source dtype (typically uint16 for medical imaging). For binary
    outputs (Otsu) we stretch {0,1} → full range so the result is still
    visible at the source's W/L.
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
