"""Unit tests for the pure parts of `SegmentationService`.

Inference itself needs a downloaded checkpoint, so the model is stubbed; what
is exercised here is the content-addressed cache key, the ROI coordinate
round-trip, and the mask → derived-DICOM encode tail.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from app.schemas.processing import ROI
from app.services import segmentation_service as seg
from app.services.segmentation_service import SegmentationService, _offset_annotation

MODEL = "unet_bce_dice_512_inbreast"
SOURCE_KEY = "owner/study/series/sop.dcm"


def _key(**kwargs: Any) -> str:
    args: dict[str, Any] = {
        "source_key": SOURCE_KEY,
        "model_name": MODEL,
        "threshold": None,
        "min_lesion_area": None,
        "roi": None,
    }
    args.update(kwargs)
    return SegmentationService._derived_key(**args)


def test_identical_requests_share_a_key() -> None:
    assert _key() == _key()


def test_key_lands_under_the_derived_prefix() -> None:
    key = _key()
    assert key.startswith("owner/study/series/derived/sop--deep_segmentation-")
    assert key.endswith(".dcm")
    # Never collides with the source object.
    assert key != SOURCE_KEY


@pytest.mark.parametrize(
    "override",
    [
        {"model_name": "unetpp_focal_dice_256_cbis_ddsm_new"},
        {"threshold": 0.7},
        {"min_lesion_area": 32},
        {"roi": ROI(x=10, y=20, width=64, height=64)},
    ],
)
def test_every_input_participates_in_the_hash(override: dict[str, Any]) -> None:
    assert _key(**override) != _key()


def test_different_rois_do_not_collide() -> None:
    a = _key(roi=ROI(x=0, y=0, width=10, height=10))
    b = _key(roi=ROI(x=5, y=0, width=10, height=10))
    assert a != b


class _StubAlgorithm:
    """Emits a fixed 5x5 square mask plus one polygon annotation."""

    def __init__(self) -> None:
        self.threshold = 0.5
        self.min_lesion_area = 4

    def apply(self, src: Any, out: Any) -> Any:
        import torch
        from medical_image.data.annotation import Annotation, GeometryType

        h, w = src.pixel_data.shape[-2:]
        mask = torch.zeros(h, w)
        mask[4:9, 4:9] = 1.0
        out.pixel_data = mask
        out.annotations = []
        out.add_annotation(
            Annotation(
                shape=GeometryType.POLYGON,
                coordinates=[(4, 4), (8, 4), (8, 8), (4, 8)],
                label="microcalcification",
                metadata={"confidence": 0.87, "area": 25},
            )
        )
        return out


class _StubSettings:
    deep_segmentation_model_server_url = "http://models.invalid/"
    deep_segmentation_model_cache_dir = "/tmp/models"
    deep_segmentation_device = "cpu"


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> SegmentationService:
    stub = _StubAlgorithm()
    monkeypatch.setattr(seg._registry, "get", lambda *a, **k: stub)
    svc = SegmentationService.__new__(SegmentationService)
    svc._settings = _StubSettings()  # type: ignore[assignment]
    return svc


@pytest.fixture
def source_dicom() -> bytes:
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.1.2"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Rows, ds.Columns = 16, 16
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
    ds.SamplesPerPixel, ds.PixelRepresentation = 1, 0
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = (np.arange(256, dtype=np.uint16) * 7).tobytes()
    buf = io.BytesIO()
    ds.save_as(buf, write_like_original=False)
    return buf.getvalue()


def test_mask_is_written_as_an_uncompressed_derived_dicom(
    service: SegmentationService, source_dicom: bytes
) -> None:
    out_bytes, count, annotations = service._run_segmentation(source_dicom, MODEL, None, None, None)
    ds = pydicom.dcmread(io.BytesIO(out_bytes))

    assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    arr = ds.pixel_array
    assert arr.shape == (16, 16)
    assert arr.dtype == np.uint16
    # Binary {0,1} is stretched to the source dtype's full range so the mask
    # is visible at the source's window/level.
    assert arr[6, 6] == np.iinfo(np.uint16).max
    assert arr[0, 0] == 0

    assert count == 1
    assert annotations[0]["label"] == "microcalcification"
    assert annotations[0]["metadata"]["confidence"] == 0.87


def test_roi_pastes_the_mask_back_and_shifts_coordinates(
    service: SegmentationService, source_dicom: bytes
) -> None:
    roi = ROI(x=2, y=3, width=12, height=11)
    out_bytes, count, annotations = service._run_segmentation(source_dicom, MODEL, None, None, roi)
    arr = pydicom.dcmread(io.BytesIO(out_bytes)).pixel_array

    assert arr.shape == (16, 16)
    assert arr[:3, :].sum() == 0, "rows above the ROI must stay empty"
    # The model saw the crop, so its (4, 4) becomes (4+x, 4+y) full-image.
    assert annotations[0]["coordinates"][0] == [6, 7]
    assert count == 1


def test_overrides_are_restored_on_the_shared_instance(
    monkeypatch: pytest.MonkeyPatch, source_dicom: bytes
) -> None:
    """The registry hands out one algorithm per model — per-call overrides
    must not leak into the next request."""
    stub = _StubAlgorithm()
    monkeypatch.setattr(seg._registry, "get", lambda *a, **k: stub)
    svc = SegmentationService.__new__(SegmentationService)
    svc._settings = _StubSettings()  # type: ignore[assignment]

    svc._run_segmentation(source_dicom, MODEL, 0.9, 64, None)

    assert stub.threshold == 0.5
    assert stub.min_lesion_area == 4


def test_multiframe_input_is_rejected_cleanly(service: SegmentationService) -> None:
    from app.services.derived_pixels import FilterError

    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.1.2"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Rows, ds.Columns, ds.NumberOfFrames = 4, 4, 2
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
    ds.SamplesPerPixel, ds.PixelRepresentation = 1, 0
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = np.zeros(32, dtype=np.uint16).tobytes()
    buf = io.BytesIO()
    ds.save_as(buf, write_like_original=False)

    with pytest.raises(FilterError, match="single-frame"):
        service._run_segmentation(buf.getvalue(), MODEL, None, None, None)


@pytest.mark.parametrize(
    ("shape", "coords", "expected"),
    [
        ("POLYGON", [[1, 1], [3, 1], [3, 3]], [[11, 21], [13, 21], [13, 23]]),
        ("RECTANGLE", [1, 1, 3, 3], [11, 21, 13, 23]),
        ("ELLIPSE", [5, 5, 2, 3], [15, 25, 2, 3]),
    ],
)
def test_offset_annotation_shifts_each_geometry(shape: str, coords: Any, expected: Any) -> None:
    ann = {
        "shape": shape,
        "coordinates": coords,
        "label": "mass",
        "center": [2.0, 1.5],
        "bounding_box": [1, 1, 3, 3],
        "metadata": {"confidence": 0.5},
    }

    out = _offset_annotation(ann, 10, 20)

    assert out["coordinates"] == expected
    assert out["center"] == [12.0, 21.5]
    assert out["bounding_box"] == [11, 21, 13, 23]
    # Radii and payload are untouched by a translation.
    assert out["metadata"] == ann["metadata"]
    assert out["label"] == "mass"
