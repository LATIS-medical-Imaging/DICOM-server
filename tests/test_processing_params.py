"""The clustering filters must forward every tunable param, not just the first.

These guard the dispatch table in `ProcessingService._apply_to_array`: the
viewer sends `m`/`eta`/`tau`/`max_iter` and they have to reach the algorithm
constructors instead of being silently replaced by the hardcoded defaults.
"""

from __future__ import annotations

from typing import Any, ClassVar

import medical_image
import numpy as np
import pytest

from app.services.derived_pixels import FilterError
from app.services.processing_service import _apply_to_array


class _RecordingAlgorithm:
    """Stand-in that records its kwargs and emits a trivial mask."""

    last_kwargs: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs

    def apply(self, src: Any, out: Any) -> Any:
        import torch

        out.pixel_data = torch.zeros_like(src.pixel_data)
        return out


@pytest.fixture
def pixels() -> np.ndarray:
    return np.arange(64, dtype=np.uint16).reshape(8, 8)


@pytest.mark.parametrize(
    ("filter_name", "class_name", "params", "expected"),
    [
        (
            "kmeans",
            "KMeansAlgorithm",
            {"k": 5, "max_iter": 250, "tol": 1e-5},
            {"k": 5, "max_iter": 250, "tol": 1e-5, "device": "cpu"},
        ),
        (
            "fcm",
            "FCMAlgorithm",
            {"c": 3, "m": 1.6, "max_iter": 40, "tol": 1e-2},
            {"c": 3, "m": 1.6, "max_iter": 40, "tol": 1e-2, "device": "cpu"},
        ),
        (
            "pfcm",
            "PFCMAlgorithm",
            {"c": 4, "m": 2.5, "eta": 3.0, "a": 2.0, "b": 6.0, "tau": 0.2, "max_iter": 60},
            {
                "c": 4,
                "m": 2.5,
                "eta": 3.0,
                "a": 2.0,
                "b": 6.0,
                "tau": 0.2,
                "max_iter": 60,
                "device": "cpu",
            },
        ),
    ],
)
def test_params_reach_the_algorithm(
    monkeypatch: pytest.MonkeyPatch,
    pixels: np.ndarray,
    filter_name: str,
    class_name: str,
    params: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    recorder = type(class_name, (_RecordingAlgorithm,), {})
    monkeypatch.setattr(medical_image, class_name, recorder)

    _apply_to_array(pixels, filter_name, params)

    assert recorder.last_kwargs == expected


@pytest.mark.parametrize(
    ("filter_name", "class_name", "expected"),
    [
        ("kmeans", "KMeansAlgorithm", {"k": 2, "max_iter": 100, "tol": 1e-4, "device": "cpu"}),
        (
            "fcm",
            "FCMAlgorithm",
            {"c": 2, "m": 2.0, "max_iter": 100, "tol": 1e-3, "device": "cpu"},
        ),
        (
            "pfcm",
            "PFCMAlgorithm",
            {
                "c": 2,
                "m": 2.0,
                "eta": 2.0,
                "a": 1.0,
                "b": 4.0,
                "tau": 0.04,
                "max_iter": 100,
                "device": "cpu",
            },
        ),
    ],
)
def test_defaults_match_the_library_defaults(
    monkeypatch: pytest.MonkeyPatch,
    pixels: np.ndarray,
    filter_name: str,
    class_name: str,
    expected: dict[str, Any],
) -> None:
    """An empty `params` must reproduce the algorithm classes' own defaults."""
    recorder = type(class_name, (_RecordingAlgorithm,), {})
    monkeypatch.setattr(medical_image, class_name, recorder)

    _apply_to_array(pixels, filter_name, {})

    assert recorder.last_kwargs == expected


def test_untouched_filters_still_take_their_single_param(
    monkeypatch: pytest.MonkeyPatch, pixels: np.ndarray
) -> None:
    recorder = type("TopHatAlgorithm", (_RecordingAlgorithm,), {})
    monkeypatch.setattr(medical_image, "TopHatAlgorithm", recorder)

    _apply_to_array(pixels, "top_hat", {"radius": 9})

    assert recorder.last_kwargs == {"radius": 9, "device": "cpu"}


def test_bad_param_value_becomes_a_filter_error(pixels: np.ndarray) -> None:
    """The endpoint maps FilterError to a 400 — no stack trace to the client."""
    with pytest.raises(FilterError):
        _apply_to_array(pixels, "kmeans", {"k": "not-a-number"})


def test_unknown_filter_is_rejected(pixels: np.ndarray) -> None:
    with pytest.raises(FilterError, match="Unknown filter"):
        _apply_to_array(pixels, "nope", {})
