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
        # Keyed by device too: an OOM fallback loads a second CPU copy of the
        # same checkpoint, and it must not evict the GPU-resident one.
        key = f"{model_name}@{device}"
        model = self._models.get(key)
        if model is not None:
            return model

        # Serialize loads so two concurrent first-requests don't both download
        # and construct the same checkpoint.
        with self._lock:
            model = self._models.get(key)
            if model is not None:
                return model
            from medical_image.algorithms.deep_segmentation import DeepSegmentationAlgorithm

            logger.info("segmentation_model_loading", model=model_name, device=device)
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
            self._models[key] = model
            logger.info("segmentation_model_loaded", model=model_name, device=device)
            return model


_registry = _ModelRegistry()


def preload_models(settings: Settings) -> list[str]:
    """Load the configured checkpoints into this process's registry.

    Called from the app lifespan on a background task. Without it the first
    segmentation request after every deploy pays the torch import, an HTTP
    fetch from the model server and a `torch.load` — 10-30 s landing on
    whichever doctor happens to click first.
    """
    names = [n.strip() for n in settings.deep_segmentation_preload_models.split(",") if n.strip()]
    loaded = []
    for name in names:
        try:
            _registry.get(
                name,
                settings.deep_segmentation_model_server_url,
                settings.deep_segmentation_model_cache_dir,
                resolve_device(settings.deep_segmentation_device),
            )
            loaded.append(name)
        except Exception as exc:
            # A preload is an optimisation; the model server being down at boot
            # must not stop the API from serving everything else.
            logger.warning("segmentation_preload_failed", model=name, error=str(exc))
    return loaded


class SegmentationService:
    """Runs `DeepSegmentationAlgorithm` over an instance's pixel data."""

    def __init__(
        self, db: AsyncSession | None, storage: StorageService, settings: Settings
    ) -> None:
        # `db` is None in the Celery path: the task resolves the source object
        # key itself with a sync session, and the blocking core never touches
        # the database.
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
    ) -> tuple[str, bool, int, list[dict[str, Any]], bool]:
        """Returns (derived_key, was_cached, lesion_count, annotations, has_png).

        Kept for callers that want the whole thing inline; the endpoint
        enqueues `apply_to_key` instead.
        """
        if self._db is None:
            raise FilterError("No database session: use apply_to_key from a worker.")
        instance = await load_instance(self._db, instance_id)
        return await asyncio.to_thread(
            self.apply_to_key, instance.file_path, model_name, threshold, min_lesion_area, roi
        )

    def apply_to_key(
        self,
        source_key: str,
        model_name: str,
        threshold: float | None = None,
        min_lesion_area: int | None = None,
        roi: ROI | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> tuple[str, bool, int, list[dict[str, Any]], bool]:
        """Blocking core: cache probe, download, infer, upload mask + sidecars.

        Takes the source object key rather than an instance id so the Celery
        task can reuse it without a second DB round-trip.
        """
        derived_key = self._derived_key(source_key, model_name, threshold, min_lesion_area, roi)
        bucket = self._settings.minio_bucket_dicom
        timer = StageTimer("segmentation", model=model_name, derived_key=derived_key)

        with timer.stage("cache_probe"):
            cached = self._read_sidecar(bucket, derived_key)
        if cached is not None:
            timer.log("segmentation_cached", lesions=cached[0])
            return derived_key, True, cached[0], cached[1], cached[2]

        if on_stage:
            on_stage("downloading")
        with timer.stage("download"):
            source_bytes = self._storage.get_object_bytes(bucket, source_key)

        if on_stage:
            on_stage("inferring")
        derived_bytes, lesion_count, annotations, mask = self._run_segmentation(
            source_bytes, model_name, threshold, min_lesion_area, roi, timer
        )

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
            has_png = self._put_mask_png(bucket, derived_key, mask)
            sidecar_bytes = json.dumps(
                {
                    "lesion_count": lesion_count,
                    "annotations": annotations,
                    "has_png": has_png,
                }
            ).encode()
            self._storage.put_object(
                bucket=bucket,
                key=f"{derived_key}.json",
                data=io.BytesIO(sidecar_bytes),
                length=len(sidecar_bytes),
                content_type="application/json",
            )

        timer.log("segmentation_applied", lesions=lesion_count, size=len(derived_bytes))
        return derived_key, False, lesion_count, annotations, has_png

    def cached_result(
        self,
        source_key: str,
        model_name: str,
        threshold: float | None,
        min_lesion_area: int | None,
        roi: ROI | None,
    ) -> tuple[str, int, list[dict[str, Any]], bool] | None:
        """The cached (key, lesion_count, annotations, has_png) if present, else None."""
        derived_key = self._derived_key(source_key, model_name, threshold, min_lesion_area, roi)
        cached = self._read_sidecar(self._settings.minio_bucket_dicom, derived_key)
        if cached is None:
            return None
        return derived_key, cached[0], cached[1], cached[2]

    def _read_sidecar(
        self, bucket: str, derived_key: str
    ) -> tuple[int, list[dict[str, Any]], bool] | None:
        """One GET, not two HEADs and a GET.

        The sidecar is written after the mask, so its presence already implies
        the mask exists — probing both cost three serial round-trips to storage
        on what should be the fastest path in the pipeline.
        """
        raw = self._storage.get_object_bytes_or_none(bucket, f"{derived_key}.json")
        if raw is None:
            return None
        sidecar = json.loads(raw)
        # Masks written before the PNG overlay existed have no flag — reporting
        # False keeps the endpoint from handing the viewer a URL that 404s.
        return int(sidecar["lesion_count"]), sidecar["annotations"], bool(sidecar.get("has_png"))

    def _put_mask_png(self, bucket: str, derived_key: str, mask: np.ndarray) -> bool:
        """Write the mask as a PNG next to the derived DICOM.

        The DICOM carries the mask stretched across the full uint16 range: ~24 MB
        for a mammogram, to convey one bit per pixel. The PNG is the same mask in
        kilobytes. Written additively so the viewer can move to it (or to the
        lesion polygons, which the response already carries) without a flag day;
        once nothing reads the DICOM, that write can go.
        """
        try:
            from PIL import Image

            binary = (mask > 0).astype(np.uint8) * 255
            buffer = io.BytesIO()
            Image.fromarray(binary, mode="L").save(buffer, format="PNG", optimize=True)
            payload = buffer.getvalue()
            self._storage.put_object(
                bucket=bucket,
                key=f"{derived_key}.png",
                data=io.BytesIO(payload),
                length=len(payload),
                content_type="image/png",
            )
            return True
        except Exception as exc:
            # An overlay convenience must never fail a request whose mask and
            # annotations were produced correctly.
            logger.warning("segmentation_mask_png_failed", derived_key=derived_key, error=str(exc))
            return False

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
        timer: StageTimer,
    ) -> tuple[bytes, int, list[dict[str, Any]], np.ndarray]:
        """Decode → infer → re-encode the mask as a derived DICOM.

        With an ROI the model sees only the crop; annotation coordinates are
        shifted back into full-image space so the viewer can draw them without
        knowing the ROI was used.

        Returns the encoded DICOM plus the full-image binary mask, which the
        caller writes out as a PNG overlay.
        """
        with timer.stage("decode"):
            ds = pydicom.dcmread(io.BytesIO(source_bytes))
            try:
                original = ds.pixel_array
            except RuntimeError as exc:
                raise FilterError(f"Cannot decode pixel data: {exc}") from exc
        if original.ndim != 2:
            raise FilterError("Only single-frame 2D images are supported for segmentation.")

        with timer.stage("inference"):
            if roi is None:
                mask, annotations = self._infer(original, model_name, threshold, min_lesion_area)
                full_mask = mask
                scaled = rescale_to_dtype(mask, original.dtype)
            else:
                y0, x0, y1, x1 = clamp_roi(roi, original.shape)
                crop = original[y0:y1, x0:x1]
                if crop.size == 0:
                    raise FilterError("ROI is empty after clamping to image bounds.")
                crop_mask, annotations = self._infer(crop, model_name, threshold, min_lesion_area)
                full_mask = np.zeros(original.shape, dtype=crop_mask.dtype)
                full_mask[y0:y1, x0:x1] = crop_mask
                scaled = np.zeros_like(original)
                scaled[y0:y1, x0:x1] = rescale_to_dtype(crop_mask, original.dtype)
                annotations = [_offset_annotation(a, x0, y0) for a in annotations]

        with timer.stage("encode"):
            ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
            ds.PixelData = scaled.tobytes()
            ds.Rows = scaled.shape[0]
            ds.Columns = scaled.shape[1]

            out = io.BytesIO()
            ds.save_as(out, write_like_original=False)
            return out.getvalue(), len(annotations), annotations, full_mask

    def _infer(
        self,
        pixels: np.ndarray,
        model_name: str,
        threshold: float | None,
        min_lesion_area: int | None,
    ) -> tuple[np.ndarray, list[dict[str, Any]]]:
        import torch
        from medical_image import InMemoryImage

        configure_threads(self._settings.torch_num_threads)
        device = resolve_device(self._settings.deep_segmentation_device)
        algo = _registry.get(
            model_name,
            self._settings.deep_segmentation_model_server_url,
            self._settings.deep_segmentation_model_cache_dir,
            device,
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
        except torch.cuda.OutOfMemoryError:
            # A 6 GB laptop GPU can run out on a full-resolution mammogram.
            # Retrying on a CPU-resident copy is slow but beats failing.
            logger.warning("segmentation_cuda_oom_retry_on_cpu", model=model_name)
            torch.cuda.empty_cache()
            out = InMemoryImage(source_image=src)
            cpu_algo = _registry.get(
                model_name,
                self._settings.deep_segmentation_model_server_url,
                self._settings.deep_segmentation_model_cache_dir,
                "cpu",
            )
            cpu_algo.threshold, cpu_algo.min_lesion_area = algo.threshold, algo.min_lesion_area
            try:
                cpu_algo.apply(src, out)
            except Exception as exc:
                raise FilterError(f"Segmentation model {model_name!r} failed: {exc}") from exc
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
