"""Apply `medical-image-std` filters to a stored DICOM instance.

The pipeline downloads the source DICOM from object storage, runs a CPU
filter via `medical-image-std`, and writes a derived DICOM back under a
deterministic key. Identical (filter, params) requests therefore hit the
existing object instead of recomputing — the endpoint can be called
repeatedly while the user toggles filters in the viewer.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
from collections.abc import Callable
from typing import Any

import numpy as np
import pydicom
from pydicom.uid import ExplicitVRLittleEndian
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.timing import StageTimer
from app.core.torch_runtime import configure_threads, resolve_device
from app.db.models.instance import Instance
from app.schemas.processing import ROI
from app.services.derived_pixels import (
    DERIVED_PREFIX,
    FilterError,
    clamp_roi,
    load_instance,
    rescale_to_dtype,
)
from app.services.storage_service import StorageService

logger = get_logger(__name__)

__all__ = ["FilterError", "ProcessingService"]


class ProcessingService:
    """Server-side filter pipeline.

    Lazily imports `medical-image-std` so unit tests that don't touch
    processing don't pay the torch import cost on collection.
    """

    def __init__(
        self, db: AsyncSession | None, storage: StorageService, settings: Settings
    ) -> None:
        # `db` is None in the Celery path: the task resolves the source object
        # key itself with a sync session, and the blocking core never touches
        # the database.
        self._db = db
        self._storage = storage
        self._settings = settings

    async def apply(
        self,
        instance_id: uuid.UUID,
        filter_name: str,
        params: dict[str, Any],
        roi: ROI | None = None,
    ) -> tuple[str, bool]:
        """Apply a filter to the instance's pixel data.

        Returns (derived_object_key, was_cached). Kept for callers that want
        the whole thing inline; the endpoint enqueues `apply_to_key` instead.
        """
        instance = await self._load_instance(instance_id)
        # Storage I/O and the filter itself are blocking, and this coroutine
        # runs on the API's only event loop — without the hop, one PFCM pass
        # over a full mammogram stalls every other request on the process.
        return await asyncio.to_thread(
            self.apply_to_key, instance.file_path, filter_name, params, roi
        )

    def apply_to_key(
        self,
        source_key: str,
        filter_name: str,
        params: dict[str, Any],
        roi: ROI | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> tuple[str, bool]:
        """Blocking core: cache probe, download, filter, upload.

        Takes the source object key rather than an instance id so the Celery
        task can reuse it without a second DB round-trip — the endpoint already
        loaded the row to authorize the request.
        """
        derived_key = self._derived_key(source_key, filter_name, params, roi)
        bucket = self._settings.minio_bucket_dicom
        timer = StageTimer("filter", filter=filter_name, derived_key=derived_key)

        with timer.stage("cache_probe"):
            if self._storage.object_exists(bucket, derived_key):
                timer.log("processing_filter_cached")
                return derived_key, True

        if on_stage:
            on_stage("downloading")
        with timer.stage("download"):
            source_bytes = self._storage.get_object_bytes(bucket, source_key)

        if on_stage:
            on_stage("filtering")
        derived_bytes = self._run_filter(source_bytes, filter_name, params, roi, timer)

        if on_stage:
            on_stage("storing")
        with timer.stage("upload"):
            self._storage.put_object(
                bucket=bucket,
                key=derived_key,
                data=io.BytesIO(derived_bytes),
                length=len(derived_bytes),
                content_type="application/dicom",
            )

        timer.log("processing_filter_applied", size=len(derived_bytes))
        return derived_key, False

    def cached_key(
        self,
        source_key: str,
        filter_name: str,
        params: dict[str, Any],
        roi: ROI | None = None,
    ) -> str | None:
        """The derived key if it already exists, else None. One HEAD."""
        derived_key = self._derived_key(source_key, filter_name, params, roi)
        if self._storage.object_exists(self._settings.minio_bucket_dicom, derived_key):
            return derived_key
        return None

    async def _load_instance(self, instance_id: uuid.UUID) -> Instance:
        if self._db is None:
            raise FilterError("No database session: use apply_to_key from a worker.")
        return await load_instance(self._db, instance_id)

    @staticmethod
    def _derived_key(
        source_key: str,
        filter_name: str,
        params: dict[str, Any],
        roi: ROI | None,
    ) -> str:
        """Content-addressed key so repeat requests reuse the prior result.

        ROI is part of the hash — same algorithm + same params + different
        region must resolve to a different cached object.
        """
        prefix, _, filename = source_key.rpartition("/")
        sop = filename.removesuffix(".dcm") or filename
        roi_part = roi.model_dump() if roi else None
        normalized = json.dumps(
            {"params": params, "roi": roi_part}, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(f"{filter_name}|{normalized}".encode()).hexdigest()[:12]
        return f"{prefix}/{DERIVED_PREFIX}/{sop}--{filter_name}-{digest}.dcm"

    def _run_filter(
        self,
        source_bytes: bytes,
        filter_name: str,
        params: dict[str, Any],
        roi: ROI | None,
        timer: StageTimer,
    ) -> bytes:
        """Decode → apply filter via `medical-image-std` → re-encode as DICOM.

        When an ROI is given, the filter runs on the cropped region only and
        the result is pasted back into a copy of the original — pixels
        outside the ROI keep their source values.
        """
        with timer.stage("decode"):
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

        configure_threads(self._settings.torch_num_threads)
        device = resolve_device(self._settings.deep_segmentation_device)

        with timer.stage("algorithm"):
            if roi is None:
                processed = _apply_to_array(original, filter_name, params, device)
                scaled = rescale_to_dtype(processed, original.dtype)
            else:
                y0, x0, y1, x1 = clamp_roi(roi, original.shape)
                crop = original[y0:y1, x0:x1]
                if crop.size == 0:
                    raise FilterError("ROI is empty after clamping to image bounds.")
                crop_processed = _apply_to_array(crop, filter_name, params, device)
                crop_scaled = rescale_to_dtype(crop_processed, original.dtype)
                scaled = original.copy()
                scaled[y0:y1, x0:x1] = crop_scaled

        # Switch to uncompressed transfer syntax before writing back raw pixel
        # bytes — if the source used a compressed syntax (e.g. JPEG Lossless),
        # pydicom would try to encapsulate the raw bytes and fail.
        with timer.stage("encode"):
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
    device: str = "cpu",
) -> np.ndarray:
    """Dispatch to a `medical-image-std` operation and return the result as numpy.

    Each handler accepts the raw numpy array and returns a numpy float
    array — final dtype conversion happens in `_rescale_to_dtype`.

    `device` comes from `resolve_device` — these are torch algorithms, and the
    iterative clustering ones (FCM/PFCM at 100 iterations over a full
    mammogram) are exactly the workload a GPU flattens.
    """
    # Imports are local to keep cold-start fast for non-filter requests.
    import torch
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
        "top_hat": lambda: TopHatAlgorithm(
            radius=int(params.get("radius", 4)), device=device
        ).apply(src, out),
        "kmeans": lambda: KMeansAlgorithm(
            k=int(params.get("k", 2)),
            max_iter=int(params.get("max_iter", 100)),
            tol=float(params.get("tol", 1e-4)),
            device=device,
        ).apply(src, out),
        "fcm": lambda: FCMAlgorithm(
            c=int(params.get("c", 2)),
            m=float(params.get("m", 2.0)),
            max_iter=int(params.get("max_iter", 100)),
            tol=float(params.get("tol", 1e-3)),
            device=device,
        ).apply(src, out),
        "pfcm": lambda: PFCMAlgorithm(
            c=int(params.get("c", 2)),
            m=float(params.get("m", 2.0)),
            eta=float(params.get("eta", 2.0)),
            a=float(params.get("a", 1.0)),
            b=float(params.get("b", 4.0)),
            tau=float(params.get("tau", 0.04)),
            max_iter=int(params.get("max_iter", 100)),
            device=device,
        ).apply(src, out),
        "febds": lambda: FebdsAlgorithm(
            method=str(params.get("method", "dog")), device=device
        ).apply(src, out),
        "breast_mask": lambda: BreastMaskAlgorithm(
            mask_only=bool(params.get("mask_only", False)), device=device
        ).apply(src, out),
    }

    handler = handlers.get(filter_name)
    if handler is None:
        raise FilterError(f"Unknown filter: {filter_name!r}")

    try:
        handler()
    except torch.cuda.OutOfMemoryError:
        # Full-resolution clustering can exceed a small GPU. Slow beats failed.
        logger.warning("filter_cuda_oom_retry_on_cpu", filter=filter_name)
        torch.cuda.empty_cache()
        if device == "cpu":
            raise
        return _apply_to_array(pixels, filter_name, params, "cpu")
    except Exception as exc:
        raise FilterError(f"Filter '{filter_name}' failed: {exc}") from exc

    if out.pixel_data is None:
        raise FilterError(f"Filter '{filter_name}' produced no output.")

    return out.pixel_data.detach().cpu().numpy()
