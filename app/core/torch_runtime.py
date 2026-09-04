"""Torch device and thread-count resolution, shared by every pixel service.

Two things need deciding once per process and reusing everywhere:

* **Which device.** `DEEP_SEGMENTATION_DEVICE=auto` (the default) picks CUDA
  when the container can actually reach a GPU, and falls back to CPU
  otherwise. "Can reach" means both a passed-through device *and* a CUDA torch
  build — the default image ships `torch+cpu`, so a compose file that requests
  a GPU without also rebuilding with `TORCH_INDEX_URL` pointed at a CUDA index
  still resolves to CPU rather than failing at inference time.
* **How many threads.** Torch sizes its CPU thread pool from the *host's*
  processor count, ignoring the container's cgroup quota. On a 2-vCPU cloud
  slice it will happily spawn 16 OMP threads and spend the request context
  switching, so `TORCH_NUM_THREADS` caps it.
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_resolved_device: str | None = None
_threads_configured = False


def resolve_device(configured: str) -> str:
    """Map the configured device onto one this process can actually use.

    Anything other than ``auto`` is honoured as-is except ``cuda`` on a host
    without one, which degrades to CPU instead of raising at inference time.
    """
    global _resolved_device

    if _resolved_device is not None:
        return _resolved_device

    with _lock:
        if _resolved_device is not None:
            return _resolved_device

        requested = (configured or "auto").strip().lower()
        available = _cuda_available()

        if requested == "auto":
            device = "cuda" if available else "cpu"
        elif requested.startswith("cuda") and not available:
            logger.warning("torch_cuda_requested_but_unavailable", requested=requested)
            device = "cpu"
        else:
            device = requested

        logger.info(
            "torch_device_resolved",
            configured=configured,
            device=device,
            cuda_available=available,
        )
        _resolved_device = device
        return device


def configure_threads(num_threads: int) -> None:
    """Cap torch's CPU thread pool. ``0`` leaves torch's own default alone."""
    global _threads_configured

    if _threads_configured or num_threads <= 0:
        return

    with _lock:
        if _threads_configured:
            return
        import torch

        torch.set_num_threads(num_threads)
        _threads_configured = True
        logger.info("torch_threads_configured", num_threads=num_threads)


def run_with_cpu_fallback(fn: Any, device: str) -> Any:
    """Run ``fn(device)``, retrying on CPU if the GPU runs out of memory.

    A 6 GB laptop GPU handles most single-slice work but not a full-resolution
    mammogram under every algorithm. Falling back beats a 500 — the request is
    slower, not failed.
    """
    if device == "cpu":
        return fn(device)

    import torch

    try:
        return fn(device)
    except torch.cuda.OutOfMemoryError:
        logger.warning("torch_cuda_oom_falling_back_to_cpu", device=device)
        torch.cuda.empty_cache()
        return fn("cpu")


def _cuda_available() -> bool:
    try:
        import torch
    except Exception as exc:
        logger.warning("torch_import_failed", error=str(exc))
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception as exc:
        # A driver/runtime mismatch raises here rather than returning False.
        logger.warning("torch_cuda_probe_failed", error=str(exc))
        return False


def device_report(configured: str) -> dict[str, Any]:
    """Diagnostic snapshot for logs and the health endpoint."""
    report: dict[str, Any] = {"configured": configured, "resolved": resolve_device(configured)}
    try:
        import torch

        report["torch_version"] = torch.__version__
        report["cuda_build"] = torch.version.cuda
        report["cuda_available"] = torch.cuda.is_available()
        report["num_threads"] = torch.get_num_threads()
        if report["cuda_available"]:
            report["gpu_name"] = torch.cuda.get_device_name(0)
            report["gpu_count"] = torch.cuda.device_count()
    except Exception as exc:
        report["error"] = str(exc)
    return report
