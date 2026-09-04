"""Stage timing for the pixel pipelines.

Both derived-pixel services used to emit a single log line after the work was
done, which made it impossible to tell whether a slow request was spent in
object storage, in the decode, in the algorithm or in the upload. Every stage
now logs its own duration under a shared `job` id so one request's stages can
be grepped together.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class StageTimer:
    """Accumulates per-stage durations for one pipeline run."""

    def __init__(self, pipeline: str, **context: Any) -> None:
        self._pipeline = pipeline
        self._context = context
        self._stages: dict[str, float] = {}
        self._started = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            # Repeated stages (two storage reads, say) accumulate rather than
            # overwrite, so the totals still add up to the wall clock.
            self._stages[name] = self._stages.get(name, 0.0) + elapsed_ms
            logger.debug(
                "pipeline_stage",
                pipeline=self._pipeline,
                stage=name,
                ms=round(elapsed_ms, 1),
                **self._context,
            )

    def summary(self) -> dict[str, float]:
        """Per-stage milliseconds plus the total, rounded for logging."""
        summary = {f"ms_{name}": round(ms, 1) for name, ms in self._stages.items()}
        summary["ms_total"] = round((time.perf_counter() - self._started) * 1000, 1)
        return summary

    def log(self, event: str, **extra: Any) -> None:
        logger.info(event, pipeline=self._pipeline, **self._context, **self.summary(), **extra)
