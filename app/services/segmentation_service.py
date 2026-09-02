"""Deep-learning lesion segmentation on a stored DICOM instance.

Mirrors `ProcessingService`'s content-addressed cache, but the output is a
binary mask *plus* a list of per-lesion annotations, so a cache hit has to
restore more than the derived object: a small JSON sidecar is written next to
the mask and read back when the mask already exists.

Loaded checkpoints are cached in-process, keyed by model name — a checkpoint
costs a download plus a `torch.load`, far too much to pay per request. Like
`WebSocketHub`'s registry the cache is per worker process, which is fine while
the API runs as a small number of replicas.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
import time
import uuid
from typing import Any

import numpy as np
import pydicom
from pydicom.uid import ExplicitVRLittleEndian
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
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

_MODEL_LIST_TTL_SECONDS = 300


class SegmentationModelError(RuntimeError):
    """The model could not be listed, downloaded or loaded.

    Distinct from `FilterError` (a bad request): this is the model server or
    the local checkpoint cache failing, so it maps to 503, not 400.
    """


class _ModelRegistry:
    """Process-local cache of loaded `DeepSegmentationAlgorithm` instances."""

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}
        self._model_list: tuple[float, list[dict[str, Any]]] | None = None
        self._lock = threading.Lock()

    def list_models(self, server_url: str) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = self._model_list
        if cached is not None and now - cached[0] < _MODEL_LIST_TTL_SECONDS:
            return cached[1]

        from medical_image.algorithms.deep_segmentation import DeepSegmentationAlgorithm

        models = DeepSegmentationAlgorithm.list_available_models(server_url=server_url)
        self._model_list = (now, models)
        return models

    def get(self, model_name: str, server_url: str, cache_dir: str, device: str) -> Any:
        model = self._models.get(model_name)
        if model is not None:
            return model

        # Serialize loads so two concurrent first-requests don't both download
        # and construct the same checkpoint.
        with self._lock:
            model = self._models.get(model_name)
            if model is not None:
                return model
            from medical_image.algorithms.deep_segmentation import DeepSegmentationAlgorithm

            logger.info("segmentation_model_loading", model=model_name)
            try:
                model = DeepSegmentationAlgorithm.from_pretrained(
                    model_name,
                    server_url=server_url,
                    cache_dir=cache_dir,
                    device=device,
                )
            except Exception as exc:
                # Unwritable cache dir, no disk, a failed download, a corrupt
                # checkpoint. Letting this escape would 500 *and* lose the CORS
                # headers, which shows up in the browser as a CORS bug.
                raise SegmentationModelError(f"Could not load model {model_name!r}: {exc}") from exc
            self._models[model_name] = model
            logger.info("segmentation_model_loaded", model=model_name)
            return model


_registry = _ModelRegistry()


class SegmentationService:
    """Runs `DeepSegmentationAlgorithm` over an instance's pixel data."""

    def __init__(self, db: AsyncSession, storage: StorageService, settings: Settings) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings

    async def list_models(self) -> list[dict[str, Any]]:
        """Model metadata from the remote server, cached for a few minutes.

        The lookup is an outbound HTTP call to a third-party server, so it runs
        off the event loop.
        """
        try:
            return await asyncio.to_thread(
                _registry.list_models, self._settings.deep_segmentation_model_server_url
            )
        except Exception as exc:
            raise SegmentationModelError(f"Model server unreachable: {exc}") from exc

    async def apply_segmentation(
        self,
        instance_id: uuid.UUID,
        model_name: str,
        threshold: float | None = None,
        min_lesion_area: int | None = None,
        roi: ROI | None = None,
    ) -> tuple[str, bool, int, list[dict[str, Any]]]:
        """Returns (derived_object_key, was_cached, lesion_count, annotations)."""
        instance = await load_instance(self._db, instance_id)
        source_key = instance.file_path
        derived_key = self._derived_key(source_key, model_name, threshold, min_lesion_area, roi)
        sidecar_key = f"{derived_key}.json"
        bucket = self._settings.minio_bucket_dicom

        if self._storage.object_exists(bucket, derived_key) and self._storage.object_exists(
            bucket, sidecar_key
        ):
            sidecar = json.loads(self._storage.get_object_bytes(bucket, sidecar_key))
            return derived_key, True, int(sidecar["lesion_count"]), sidecar["annotations"]

        source_bytes = self._storage.get_object_bytes(bucket, source_key)
        # Sliding-window inference over a full mammogram is seconds of CPU work
        # — far heavier than the classical filters — so it runs off the loop.
        derived_bytes, lesion_count, annotations = await asyncio.to_thread(
            self._run_segmentation, source_bytes, model_name, threshold, min_lesion_area, roi
        )

        self._storage.put_object(
            bucket=bucket,
            key=derived_key,
            data=io.BytesIO(derived_bytes),
            length=len(derived_bytes),
            content_type="application/dicom",
        )
        sidecar_bytes = json.dumps(
            {"lesion_count": lesion_count, "annotations": annotations}
        ).encode()
        self._storage.put_object(
            bucket=bucket,
            key=sidecar_key,
            data=io.BytesIO(sidecar_bytes),
            length=len(sidecar_bytes),
            content_type="application/json",
        )
        logger.info(
            "segmentation_applied",
            instance_id=str(instance_id),
            model=model_name,
            derived_key=derived_key,
            lesions=lesion_count,
        )
        return derived_key, False, lesion_count, annotations

    @staticmethod
    def _derived_key(
        source_key: str,
        model_name: str,
        threshold: float | None,
        min_lesion_area: int | None,
        roi: ROI | None,
    ) -> str:
        """Content-addressed key — same instance + model + overrides + ROI reuses the mask."""
        prefix, _, filename = source_key.rpartition("/")
        sop = filename.removesuffix(".dcm") or filename
        normalized = json.dumps(
            {
                "threshold": threshold,
                "min_lesion_area": min_lesion_area,
                "roi": roi.model_dump() if roi else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(f"{model_name}|{normalized}".encode()).hexdigest()[:12]
        return f"{prefix}/{DERIVED_PREFIX}/{sop}--deep_segmentation-{digest}.dcm"

    def _run_segmentation(
        self,
        source_bytes: bytes,
        model_name: str,
        threshold: float | None,
        min_lesion_area: int | None,
        roi: ROI | None,
    ) -> tuple[bytes, int, list[dict[str, Any]]]:
        """Decode → infer → re-encode the mask as a derived DICOM.

        With an ROI the model sees only the crop; annotation coordinates are
        shifted back into full-image space so the viewer can draw them without
        knowing the ROI was used.
        """
        ds = pydicom.dcmread(io.BytesIO(source_bytes))
        try:
            original = ds.pixel_array
        except RuntimeError as exc:
            raise FilterError(f"Cannot decode pixel data: {exc}") from exc
        if original.ndim != 2:
            raise FilterError("Only single-frame 2D images are supported for segmentation.")

        if roi is None:
            mask, annotations = self._infer(original, model_name, threshold, min_lesion_area)
            scaled = rescale_to_dtype(mask, original.dtype)
        else:
            y0, x0, y1, x1 = clamp_roi(roi, original.shape)
            crop = original[y0:y1, x0:x1]
            if crop.size == 0:
                raise FilterError("ROI is empty after clamping to image bounds.")
            crop_mask, annotations = self._infer(crop, model_name, threshold, min_lesion_area)
            scaled = np.zeros_like(original)
            scaled[y0:y1, x0:x1] = rescale_to_dtype(crop_mask, original.dtype)
            annotations = [_offset_annotation(a, x0, y0) for a in annotations]

        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.PixelData = scaled.tobytes()
        ds.Rows = scaled.shape[0]
        ds.Columns = scaled.shape[1]

        out = io.BytesIO()
        ds.save_as(out, write_like_original=False)
        return out.getvalue(), len(annotations), annotations

    def _infer(
        self,
        pixels: np.ndarray,
        model_name: str,
        threshold: float | None,
        min_lesion_area: int | None,
    ) -> tuple[np.ndarray, list[dict[str, Any]]]:
        import torch  # noqa: F401  (resolves before medical_image for a clean import error)
        from medical_image import InMemoryImage

        algo = _registry.get(
            model_name,
            self._settings.deep_segmentation_model_server_url,
            self._settings.deep_segmentation_model_cache_dir,
            self._settings.deep_segmentation_device,
        )

        # Overrides are applied per call; the registry hands out one shared
        # instance per model, so restore the checkpoint's own values after.
        prev_threshold, prev_min_area = algo.threshold, algo.min_lesion_area
        if threshold is not None:
            algo.threshold = threshold
        if min_lesion_area is not None:
            algo.min_lesion_area = min_lesion_area

        src = InMemoryImage(array=pixels.astype(np.float32))
        out = InMemoryImage(source_image=src)
        try:
            algo.apply(src, out)
        except Exception as exc:
            raise FilterError(f"Segmentation model {model_name!r} failed: {exc}") from exc
        finally:
            algo.threshold, algo.min_lesion_area = prev_threshold, prev_min_area

        if out.pixel_data is None:
            raise FilterError(f"Segmentation model {model_name!r} produced no output.")

        annotations = [a.to_dict() for a in (out.annotations or [])]
        return out.pixel_data.detach().cpu().numpy(), annotations


def _offset_annotation(ann: dict[str, Any], dx: int, dy: int) -> dict[str, Any]:
    """Shift an ROI-relative annotation back into full-image pixel space."""
    shape = ann["shape"]
    coords = ann["coordinates"]
    if shape == "POLYGON":
        coords = [[p[0] + dx, p[1] + dy] for p in coords]
    elif shape == "RECTANGLE":
        coords = [coords[0] + dx, coords[1] + dy, coords[2] + dx, coords[3] + dy]
    elif shape == "ELLIPSE":
        coords = [coords[0] + dx, coords[1] + dy, coords[2], coords[3]]
    bbox = ann["bounding_box"]
    return {
        **ann,
        "coordinates": coords,
        "center": [ann["center"][0] + dx, ann["center"][1] + dy],
        "bounding_box": [bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy],
    }
